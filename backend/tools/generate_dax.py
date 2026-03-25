"""
TOOL: Generate DAX Query

This tool generates DAX queries using Azure OpenAI based on:
- User's natural language question
- Filtered schema from intent extraction

Uses Semantic Kernel for Azure OpenAI integration (same pattern as intent_extractor.py).

Usage:
    from backend.tools.generate_dax import generate_dax, DAXGenerator
    
    # Simple function call
    result = generate_dax(query, filtered_schema)
    
    # Or use the class
    generator = DAXGenerator()
    result = generator.generate(query, filtered_schema)
"""

import os
import sys
import json
import asyncio
import importlib.util
import concurrent.futures
import warnings
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Suppress httpx event loop cleanup warnings globally
warnings.filterwarnings("ignore", message=".*Event loop is closed.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*coroutine.*was never awaited.*")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Semantic Kernel for Azure OpenAI
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.contents.chat_history import ChatHistory
from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.azure_chat_prompt_execution_settings import (
    AzureChatPromptExecutionSettings,
)

# Import auth module from backend/tools (local)
from backend.tools.auth import AzureOpenAIConfig, AzureOpenAIAuthProvider, create_chat_service, get_llm_provider


def _load_prompt_from_file(filepath: Path, variable_name: str) -> str:
    """
    Dynamically load a prompt from a Python file.
    Always reads fresh from disk to avoid caching issues.
    """
    spec = importlib.util.spec_from_file_location(filepath.stem, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, variable_name)


def get_dax_generator_prompt(intent: str) -> str:
    """
    Get the DAX generator prompt for the given intent/domain.
    Loads fresh from disk each time to ensure latest changes are used.

    Convention: file = dax_generator_prompt_{domain}.py
               var  = DAX_GENERATOR_PROMPT_{DOMAIN}
    """
    domain = intent.strip().lower()
    filepath = _project_root / "backend" / "prompts" / "prompt_generator" / f"dax_generator_prompt_{domain}.py"
    variable = f"DAX_GENERATOR_PROMPT_{domain.upper()}"
    if not filepath.exists():
        raise FileNotFoundError(
            f"No generator prompt for domain '{domain}': expected {filepath}"
        )
    return _load_prompt_from_file(filepath, variable)


load_dotenv()


@dataclass
class ConversationTurn:
    """A single turn in the conversation history."""
    user_query: str
    dax_query: Optional[str] = None
    intent: Optional[str] = None
    
    def to_context_string(self) -> str:
        """Format this turn for context in the prompt."""
        result = f"User: {self.user_query}"
        if self.dax_query:
            result += f"\nGenerated DAX: {self.dax_query}"
        if self.intent:
            result += f"\nIntent: {self.intent}"
        return result


@dataclass
class DAXResult:
    """Result from DAX generation."""
    success: bool
    query: Optional[str] = None
    notes: Optional[str] = None
    used_tables: List[str] = field(default_factory=list)
    used_columns: List[str] = field(default_factory=list)
    used_measures: List[str] = field(default_factory=list)
    error: Optional[str] = None
    raw_response: Optional[str] = None
    timing: Optional[Dict[str, Any]] = None  # Add timing field
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "success": self.success,
            "query": self.query,
            "notes": self.notes,
            "used": {
                "tables": self.used_tables,
                "columns": self.used_columns,
                "measures": self.used_measures,
            } if self.success else None,
            "error": self.error,
        }
        if self.timing:
            result["timing"] = self.timing
        return result
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# Singleton instances for reuse
_generator_instance = None
_shared_chat_service = None
_shared_auth_provider = None
_shared_settings_class = None
_shared_provider_name = None


def _get_shared_chat_service(config: AzureOpenAIConfig = None):
    """Get or create shared chat service instance using the configured LLM provider."""
    global _shared_chat_service, _shared_auth_provider, _shared_settings_class, _shared_provider_name
    
    if _shared_chat_service is None:
        _shared_chat_service, _shared_settings_class, _shared_provider_name = create_chat_service(service_id="dax_generator")
        print(f"[LLM] DAX Generator using provider: {_shared_provider_name}")
    return _shared_chat_service


class DAXGenerator:
    """
    Generates DAX queries using Azure OpenAI.
    
    Uses Semantic Kernel (same pattern as intent_extractor.py).
    """
    
    def __init__(self, config: AzureOpenAIConfig = None):
        """
        Initialize the DAX generator.
        
        Args:
            config: Azure OpenAI configuration (uses defaults from env if not provided)
        """
        self.config = config or AzureOpenAIConfig()
        self._provider_name = get_llm_provider()
        
        # Initialize Semantic Kernel
        self.kernel = Kernel()
        
        # Use shared chat service (reuses connection and auth)
        self._chat_service = _get_shared_chat_service(self.config)
        self.kernel.add_service(self._chat_service)
        
        # Store the settings class for this provider
        self._settings_class = _shared_settings_class
        
        print(f"[OK] DAX Generator initialized (provider: {_shared_provider_name})")
        if self._provider_name == 'azure':
            print(f"  Model: {self.config.deployment_name}")
            print(f"  Endpoint: {self.config.endpoint}")
        else:
            from backend.tools.auth import CompassConfig
            compass_cfg = CompassConfig()
            print(f"  Model: {compass_cfg.model}")
            print(f"  Endpoint: {compass_cfg.base_url}")
    
    async def _generate_async(self, query: str, schema: str, conversation_history: List[ConversationTurn] = None, intent: str = "TRANSACTIONS") -> DAXResult:
        """
        Async implementation of DAX generation using Semantic Kernel.
        
        Args:
            query: User's natural language question
            schema: Schema content
            conversation_history: Previous conversation turns for context
            intent: The detected intent ("TRANSACTIONS" or "FEEDBACK") - determines which prompt to use
        """
        # Select prompt based on intent - loads fresh from disk each time
        dax_generator_prompt = get_dax_generator_prompt(intent)
        
        # Build prompt with schema (truncate if too long)
        schema_for_prompt = schema[:30000] if len(schema) > 30000 else schema
        
        # Build conversation context if history exists
        context_section = ""
        if conversation_history:
            context_parts = []
            for turn in conversation_history[-5:]:  # Keep last 5 turns for context
                context_parts.append(turn.to_context_string())
            if context_parts:
                context_section = "\n\nPREVIOUS CONVERSATION CONTEXT:\n" + "\n---\n".join(context_parts) + "\n\nThe current question may be a follow-up to the above. Use the context to resolve references like 'this service', 'these', 'that', etc."
        
        # Format prompt with user_query and schema (user_query passed as parameter, like validator)
        prompt = dax_generator_prompt.format(
            user_query=query,
            schema=schema_for_prompt
        )
        
        # Create chat history with system message and add the prompt as user message
        user_message = f"{prompt}{context_section}"
        chat_history = ChatHistory(system_message="You are a DAX query generator. You generate valid DAX queries based on schema information and user questions. Always respond with valid JSON.")
        chat_history.add_user_message(user_message)
        
        # Debug: print prompt length and first/last parts
        print(f"   [DEBUG] Prompt length: {len(user_message)} chars")
        print(f"   [DEBUG] Prompt start: {user_message[:500]}...")
        print(f"   [DEBUG] Prompt end: ...{user_message[-200:]}")
        
        # Call LLM with streaming to capture TTFT/TTLT
        # Uses the provider-appropriate settings class (Azure or OpenAI/Compass)
        if self._provider_name == 'compass':
            # Compass/OpenAI: use temperature, no reasoning_effort
            settings = self._settings_class(
                max_completion_tokens=4000,
                temperature=0.0,
            )
        else:
            # Azure OpenAI: gpt-5-mini uses reasoning_effort, no temperature
            settings = self._settings_class(
                max_completion_tokens=4000,
                extra_body={
                    "reasoning_effort": "low",
                },
            )
        
        # Timing variables
        request_start_time = time.time()
        ttft = None  # Time to first token
        ttlt = None  # Time to last token
        
        try:
            # Use streaming to capture TTFT and TTLT
            response_chunks = []
            async for chunk in self._chat_service.get_streaming_chat_message_content(
                chat_history=chat_history,
                settings=settings,
            ):
                current_time = time.time()
                
                # Capture TTFT on first chunk
                if ttft is None:
                    ttft = current_time - request_start_time
                    print(f"   [TIMING] TTFT (Time to First Token): {ttft:.3f}s")
                
                # Collect chunk content
                chunk_content = str(chunk) if chunk else ""
                if chunk_content:
                    response_chunks.append(chunk_content)
            
            # Capture TTLT after all chunks received
            ttlt = time.time() - request_start_time
            print(f"   [TIMING] TTLT (Time to Last Token): {ttlt:.3f}s")
            
            # Combine all chunks
            raw_response = "".join(response_chunks)
            print(f"   [DEBUG] API call successful (streamed)")
            
        except Exception as api_error:
            print(f"   [ERROR] API call failed: {api_error}")
            return DAXResult(
                success=False,
                error=f"API call failed: {api_error}"
            )
        
        # Debug: print response details
        print(f"   [DEBUG] Response content length: {len(raw_response)}")
        
        # Fallback: if response is empty or just whitespace
        if not raw_response or not raw_response.strip():
            # Try .content property
            content = getattr(response, "content", None)
            if isinstance(content, str) and content.strip():
                raw_response = content
            elif isinstance(content, list) and content:
                parts = [str(item.text if hasattr(item, "text") else item) for item in content if item]
                raw_response = "\n".join(parts)
            
            # Try items
            if not raw_response:
                items = getattr(response, "items", None)
                if items:
                    parts = [getattr(item, "text", "") or getattr(item, "content", "") for item in items]
                    raw_response = "\n".join(filter(None, parts))
            
            # Final debug dump
            if not raw_response:
                print(f"   [DEBUG] Empty response - streaming completed but no content")
        
        # Parse JSON response with timing data
        timing_data = {
            "ttft": round(ttft, 3) if ttft else None,
            "ttlt": round(ttlt, 3) if ttlt else None,
        }
        return self._parse_response(raw_response, timing_data)
    
    def _parse_response(self, raw_response: str, timing_data: Dict[str, Any] = None) -> DAXResult:
        """Parse LLM response into DAXResult object."""
        try:
            # Debug: print full raw response
            print(f"   [DEBUG] Raw LLM response (full):")
            print(f"   {repr(raw_response)}")
            
            # Clean up response (remove markdown code blocks if present)
            clean_response = raw_response.strip()
            if clean_response.startswith("```"):
                lines = clean_response.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].strip() == "```":
                    lines = lines[:-1]
                clean_response = "\n".join(lines)
            
            # Check if response is empty
            if not clean_response:
                return DAXResult(
                    success=False,
                    error="LLM returned empty response",
                    timing=timing_data,
                )
            
            parsed = json.loads(clean_response)
            
            # Check for error
            if parsed.get("error"):
                return DAXResult(
                    success=False,
                    error=parsed["error"],
                    raw_response=raw_response,
                    timing=timing_data,
                )
            
            # Extract results - check both "query" and "dax_query" keys
            dax_query = parsed.get("query") or parsed.get("dax_query") or parsed.get("dax")
            if not dax_query:
                print(f"   [DEBUG] Parsed JSON keys: {list(parsed.keys())}")
                return DAXResult(
                    success=False,
                    error=f"No query generated. Response keys: {list(parsed.keys())}",
                    raw_response=raw_response,
                    timing=timing_data,
                )
            
            used = parsed.get("used", {})
            
            return DAXResult(
                success=True,
                query=dax_query,
                notes=parsed.get("notes"),
                used_tables=used.get("tables", []),
                used_columns=used.get("columns", []),
                used_measures=used.get("measures", []),
                raw_response=raw_response,
                timing=timing_data,
            )
            
        except json.JSONDecodeError as e:
            return DAXResult(
                success=False,
                error=f"Failed to parse LLM response as JSON: {str(e)}",
                raw_response=raw_response,
                timing=timing_data,
            )
    
    def generate(self, query: str, schema: str, conversation_history: List[ConversationTurn] = None, intent: str = "TRANSACTIONS") -> DAXResult:
        """
        Generate DAX query from natural language (sync wrapper).
        
        Args:
            query: User's natural language question
            schema: Filtered schema content
            conversation_history: Previous conversation turns for follow-up context
            intent: The detected intent ("TRANSACTIONS" or "FEEDBACK") - determines which prompt to use
            
        Returns:
            DAXResult with generated query or error
        """
        if not query or not query.strip():
            return DAXResult(
                success=False,
                error="Empty query provided"
            )
        
        if not schema or not schema.strip():
            return DAXResult(
                success=False,
                error="No schema provided"
            )
        
        # Run async code in a separate thread to avoid event loop conflicts
        # (DevUI already runs an event loop, so we can't nest another one)
        def run_in_thread():
            import warnings
            # Suppress httpx cleanup warnings when loop closes
            warnings.filterwarnings("ignore", message=".*Event loop is closed.*")
            
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            
            # Suppress "Task exception was never retrieved" warnings
            def exception_handler(loop, context):
                # Ignore httpx cleanup errors
                if "Event loop is closed" in str(context.get("exception", "")):
                    return
                # Log other exceptions normally
                loop.default_exception_handler(context)
            
            new_loop.set_exception_handler(exception_handler)
            
            try:
                return new_loop.run_until_complete(self._generate_async(query, schema, conversation_history, intent))
            finally:
                # Properly cleanup pending tasks
                try:
                    pending = asyncio.all_tasks(new_loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        new_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                # Suppress the "Event loop is closed" RuntimeError from httpx cleanup
                try:
                    new_loop.close()
                except RuntimeError:
                    pass
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_thread)
            return future.result(timeout=120)  # 120 second timeout


# Singleton instance for tool use
_generator_instance = None


def get_generator() -> DAXGenerator:
    """Get or create singleton generator instance."""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = DAXGenerator()
    return _generator_instance


def generate_dax(query: str, schema: str, intent: str = "TRANSACTIONS") -> str:
    """
    Generate DAX query - Tool function for agent.
    
    Args:
        query: User's natural language question
        schema: Filtered schema content from intent extraction
        intent: The detected intent ("TRANSACTIONS" or "FEEDBACK") - determines which prompt to use
        
    Returns:
        JSON string with generated DAX query or error
    """
    generator = get_generator()
    result = generator.generate(query, schema, intent=intent)
    
    return result.to_json()


# ============================================================
# CLI for Testing
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("🔧 DAX Generator Test")
    print("=" * 60)
    
    # Test with a simple schema
    test_schema = """
TABLES:
- FactTransactions: Transaction records
  Columns: TransactionID (int64), Amount (decimal), ServiceID (int64)
  
MEASURES:
- [Total Transactions]: COUNTROWS(FactTransactions)
- [Total Amount]: SUM(FactTransactions[Amount])
"""
    
    test_query = "What is the total number of transactions?"
    
    print(f"\nQuery: {test_query}")
    print(f"\nSchema: {test_schema[:200]}...")
    print("\nGenerating DAX...")
    
    result = generate_dax(test_query, test_schema)
    print(f"\nResult:\n{result}")

