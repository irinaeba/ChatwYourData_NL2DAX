"""
Format DAX Results Tool

This tool formats DAX execution results into human-readable explanations
using Azure OpenAI LLM.

Uses Azure OpenAI with service principal authentication.
"""

import os
import json
import asyncio
import logging
import concurrent.futures
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import Semantic Kernel components
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.contents.chat_history import ChatHistory
from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.azure_chat_prompt_execution_settings import (
    AzureChatPromptExecutionSettings,
)

# Use shared auth provider for token caching
from backend.tools.auth import AzureOpenAIConfig, AzureOpenAIAuthProvider, create_chat_service, get_llm_provider

# Import the answer formatter prompt
from backend.prompts.answer_formatter_prompt import ANSWER_FORMATTER_PROMPT

logger = logging.getLogger(__name__)

# ============================================================
# Performance Constants
# ============================================================
MAX_ROWS_TO_SEND = 15  # Truncate data before sending to LLM
SIMPLE_RESULT_THRESHOLD = 50  # Use fast programmatic formatting for <= this many rows
MAX_COMPLETION_TOKENS = 700  # Reduced from 1000 - output is structured
MAX_TABLE_DISPLAY_ROWS = 25  # Max rows to show in the markdown table


# ============================================================
# Data Classes
# ============================================================
@dataclass
class FormattedResult:
    """Result of formatting DAX results."""
    success: bool
    formatted: Optional[str] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "formatted": self.formatted,
            "summary": self.summary,
            "error": self.error,
        }
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)


# ============================================================
# DAX Results Formatter
# ============================================================
class DAXResultsFormatter:
    """
    Formats DAX execution results using Azure OpenAI LLM.
    
    Creates human-readable explanations, summaries, and formatted tables
    from raw DAX query results.
    """
    
    def __init__(self):
        """Initialize the formatter."""
        self._kernel: Optional[Kernel] = None
        self._chat_history: Optional[ChatHistory] = None
        self._chat_service = None
        self._settings_class = None
        self._provider_name = None
        self._initialized = False
    
    def _validate_config(self) -> None:
        """Validate required configuration based on provider."""
        provider = get_llm_provider()
        if provider == 'compass':
            from backend.tools.auth import CompassConfig
            CompassConfig().validate()
        else:
            AzureOpenAIConfig().validate()
    
    def _ensure_initialized(self) -> None:
        """Lazy initialization of the kernel and chat service."""
        if self._initialized:
            return
        
        self._validate_config()
        
        # Initialize Semantic Kernel
        self._kernel = Kernel()
        
        # Create chat service using factory (Azure or Compass)
        self._chat_service, self._settings_class, self._provider_name = create_chat_service(service_id="results_formatter")
        self._kernel.add_service(self._chat_service)
        
        # Initialize chat history with system prompt
        self._chat_history = ChatHistory(system_message=ANSWER_FORMATTER_PROMPT)
        
        self._initialized = True
        print(f"[OK] Results Formatter initialized (provider: {self._provider_name})")
    
    def format(
        self, 
        user_query: str, 
        dax_query: str, 
        results: Dict[str, Any]
    ) -> FormattedResult:
        """
        Format DAX execution results into human-readable text.
        
        Args:
            user_query: The original user question
            dax_query: The DAX query that was executed
            results: Results from DAX execution (columns, data, row_count)
            
        Returns:
            FormattedResult with formatted text and summary
        """
        try:
            columns = results.get("columns", [])
            data = results.get("data", [])
            row_count = results.get("row_count", len(data))

            # Cross-domain: multiple result sets described as markdown sections
            cross_domain_text = results.get("_cross_domain_text")
            if cross_domain_text:
                return self._format_cross_domain(user_query, dax_query, cross_domain_text, row_count)
            
            # Optimization: Use programmatic formatting for simple results (1-3 rows)
            if row_count <= SIMPLE_RESULT_THRESHOLD:
                formatted = self._format_simple_result(user_query, dax_query, columns, data, row_count)
                return FormattedResult(
                    success=True,
                    formatted=formatted,
                    summary=self._extract_summary(formatted),
                )
            
            # For larger results, use LLM but truncate data first
            self._ensure_initialized()
            
            # Clear chat history for each format call to avoid context bloat
            self._chat_history = ChatHistory(system_message=ANSWER_FORMATTER_PROMPT)
            
            # Optimization: Truncate data to MAX_ROWS_TO_SEND before serializing
            truncated_data = data[:MAX_ROWS_TO_SEND] if len(data) > MAX_ROWS_TO_SEND else data
            truncated_results = {
                "columns": columns,
                "data": truncated_data,
                "total_rows": row_count,
                "showing_rows": len(truncated_data),
            }
            results_json = self._serialize_results(truncated_results)
            
            # Build prompt (simplified) - DAX is shown separately in UI toggle
            prompt = f"""Question: {user_query}

Results ({row_count} rows, showing {len(truncated_data)}):
{results_json}

Format with: ### Answer, ### Results (table), ### Explanation (bullets). Do NOT include DAX query."""

            self._chat_history.add_user_message(prompt)
            formatted_answer = self._run_async(self._get_response_async())
            
            return FormattedResult(
                success=True,
                formatted=formatted_answer,
                summary=self._extract_summary(formatted_answer),
            )
            
        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            logger.error(f"Formatter error: {error_msg}")
            return FormattedResult(
                success=False,
                error=str(e),
            )
    
    def _serialize_results(self, results: Any) -> str:
        """Convert results to JSON string for LLM consumption."""
        if isinstance(results, dict):
            if "columns" in results and "data" in results:
                return json.dumps({
                    "columns": results["columns"],
                    "data": results["data"]
                }, default=str)
            else:
                return json.dumps(results, default=str)
        elif isinstance(results, list):
            return json.dumps(results, default=str)
        else:
            return json.dumps({"raw": str(results)}, default=str)

    def _format_cross_domain(
        self,
        user_query: str,
        dax_query: str,
        cross_domain_text: str,
        total_rows: int,
    ) -> FormattedResult:
        """
        Format cross-domain results where each domain's data is presented
        as a separate markdown section.  Uses the LLM to produce one
        unified answer that covers all domains.
        """
        try:
            self._ensure_initialized()
            self._chat_history = ChatHistory(system_message=ANSWER_FORMATTER_PROMPT)

            prompt = f"""Question: {user_query}

This question required data from multiple domains.  Here are the results from each domain:

{cross_domain_text}

Combine ALL results into one unified answer.
Format with: ### Answer (natural-language summary of ALL results), ### Results (one table per domain — keep them separate), ### Explanation (bullets).
Do NOT include DAX query. Do NOT omit any domain's results."""

            self._chat_history.add_user_message(prompt)
            formatted_answer = self._run_async(self._get_response_async())

            return FormattedResult(
                success=True,
                formatted=formatted_answer,
                summary=self._extract_summary(formatted_answer),
            )
        except Exception as e:
            logger.error(f"Cross-domain format error: {e}")
            return FormattedResult(success=False, error=str(e))
    
    def _format_simple_result(
        self,
        user_query: str,
        dax_query: str,
        columns: List[str],
        data: List[List[Any]],
        row_count: int,
    ) -> str:
        """
        Format results programmatically without an LLM call.
        Handles 0 to ~50 rows.  Saves 3-5 seconds per query.
        """
        # -- Natural-language answer --
        if row_count == 0:
            answer = "The query returned no data."
            table = "No data returned."
        else:
            clean = lambda c: str(c).replace('[', '').replace(']', '').replace('_', ' ')

            if row_count == 1 and len(columns) == 1:
                # Single scalar
                answer = f"The {clean(columns[0])} is **{data[0][0]}**."
            elif row_count == 1:
                # Single row, multiple columns
                parts = [f"**{clean(columns[i])}**: {data[0][i]}" for i in range(len(columns))]
                answer = "The result shows " + ", ".join(parts) + "."
            elif row_count <= 5 and len(columns) <= 3:
                # Small result — mention first+last and range
                metric_col = columns[-1]  # usually the value column
                first_val = data[0][-1]
                last_val = data[-1][-1]
                label_col = columns[0] if len(columns) > 1 else None
                first_label = data[0][0] if label_col else "first"
                last_label = data[-1][0] if label_col else "last"
                answer = (
                    f"The query returned {row_count} results. "
                    f"{clean(metric_col)} ranges from **{first_val}** "
                    f"({first_label}) to **{last_val}** ({last_label})."
                )
            else:
                # Larger tabular result
                answer = f"The query returned **{row_count}** results across {len(columns)} columns."

            # -- Markdown table (cap display rows) --
            show_data = data[:MAX_TABLE_DISPLAY_ROWS]
            header = "| " + " | ".join(str(c) for c in columns) + " |"
            separator = "|" + "|".join("---" for _ in columns) + "|"
            rows_md = []
            for row in show_data:
                row_str = "| " + " | ".join(
                    str(v) if v is not None else "" for v in row
                ) + " |"
                rows_md.append(row_str)
            table = "\n".join([header, separator] + rows_md)
            if row_count > MAX_TABLE_DISPLAY_ROWS:
                table += f"\n\n*...showing {MAX_TABLE_DISPLAY_ROWS} of {row_count} rows*"

        # -- Explanation bullets --
        explanation = f"- Query returned {row_count} row(s)."

        formatted = f"""### Answer:

{answer}

### Results:

{table}

### Explanation:

{explanation}
"""
        return formatted
    
    def _extract_summary(self, formatted_text: str) -> str:
        """Extract first sentence or summary from formatted answer."""
        summary = formatted_text.split('\n')[0][:200].strip()
        if not summary.endswith('.'):
            summary = summary.rsplit(' ', 1)[0] + '...'
        return summary
    
    async def _get_response_async(self) -> str:
        """Async helper to get LLM response."""
        # Use provider-appropriate settings
        if self._provider_name == 'compass':
            settings = self._settings_class(
                max_completion_tokens=MAX_COMPLETION_TOKENS,
                temperature=0.0,
            )
        else:
            settings = self._settings_class(
                max_completion_tokens=MAX_COMPLETION_TOKENS,
                extra_body={
                    "reasoning_effort": "low",
                },
            )
        
        response = await self._chat_service.get_chat_message_content(
            chat_history=self._chat_history,
            settings=settings,
        )
        
        return str(response.content).strip()
    
    def _run_async(self, coro):
        """Helper to run async code from sync context."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Create a new loop in a separate thread
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    def run_in_new_loop():
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        try:
                            return new_loop.run_until_complete(coro)
                        finally:
                            new_loop.close()
                    return executor.submit(run_in_new_loop).result()
            else:
                return loop.run_until_complete(coro)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
    
    def clear_history(self) -> None:
        """Clear conversation history for a fresh start."""
        if self._chat_history:
            self._chat_history = ChatHistory(system_message=ANSWER_FORMATTER_PROMPT)


# ============================================================
# Singleton and Factory Functions
# ============================================================
_global_formatter: Optional[DAXResultsFormatter] = None


def get_formatter() -> DAXResultsFormatter:
    """Get or create the global results formatter."""
    global _global_formatter
    if _global_formatter is None:
        _global_formatter = DAXResultsFormatter()
    return _global_formatter


def format_dax_results(
    user_query: str,
    dax_query: str,
    columns: List[str],
    data: List[List[Any]],
    row_count: int = None,
) -> FormattedResult:
    """
    Format DAX execution results into human-readable text.
    
    This is the main entry point for the tool. Uses a singleton formatter
    to maintain conversation history across multiple calls.
    
    Args:
        user_query: The original user question
        dax_query: The DAX query that was executed
        columns: List of column names from the results
        data: List of rows (each row is a list of values)
        row_count: Number of rows (optional, calculated from data if not provided)
        
    Returns:
        FormattedResult with formatted text and summary
        
    Example:
        >>> result = format_dax_results(
        ...     user_query="What is total revenue by year?",
        ...     dax_query="EVALUATE SUMMARIZE(...)",
        ...     columns=["Year", "TotalRevenue"],
        ...     data=[[2024, 1000000], [2025, 1200000]],
        ... )
        >>> if result.success:
        ...     print(result.formatted)
    """
    formatter = get_formatter()
    
    # Build results dict in expected format
    results = {
        "columns": columns,
        "data": data,
        "row_count": row_count if row_count is not None else len(data),
    }
    
    return formatter.format(user_query, dax_query, results)


def cleanup_formatter() -> None:
    """Clean up the global formatter."""
    global _global_formatter
    if _global_formatter:
        _global_formatter.clear_history()
        _global_formatter = None
