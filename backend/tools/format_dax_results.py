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
SIMPLE_RESULT_THRESHOLD = 3  # Use programmatic formatting for <= this many rows
MAX_COMPLETION_TOKENS = 700  # Reduced from 1000 - output is structured


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
    
    def _format_simple_result(
        self,
        user_query: str,
        dax_query: str,
        columns: List[str],
        data: List[List[Any]],
        row_count: int,
    ) -> str:
        """
        Format simple results (1-3 rows) programmatically without LLM.
        This saves ~4-5 seconds per call for simple queries.
        """
        # Build answer based on data
        if row_count == 0:
            answer = "The query returned no data."
            table = "No data returned."
        else:
            # Generate natural language answer from first row
            if row_count == 1 and len(columns) == 1:
                # Single value result
                value = data[0][0]
                col_name = columns[0].replace('[', '').replace(']', '').replace('_', ' ')
                answer = f"The {col_name} is {value}."
            elif row_count == 1:
                # Single row with multiple columns
                parts = [f"{columns[i].replace('[', '').replace(']', '')}: {data[0][i]}" for i in range(min(3, len(columns)))]
                answer = "The result shows " + ", ".join(parts) + "."
            else:
                # Multiple rows (2-3)
                answer = f"The query returned {row_count} results."
            
            # Build markdown table
            header = "| " + " | ".join(str(c) for c in columns) + " |"
            separator = "|" + "|".join("---" for _ in columns) + "|"
            rows = []
            for row in data:
                row_str = "| " + " | ".join(str(v) if v is not None else "" for v in row) + " |"
                rows.append(row_str)
            table = "\n".join([header, separator] + rows)
        
        # Build explanation
        explanation = f"- Query returned {row_count} row(s)."
        
        # Format the complete response (DAX shown separately in UI toggle)
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
