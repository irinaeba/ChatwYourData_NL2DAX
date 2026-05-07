"""
TOOL: Validate DAX Query

This tool validates DAX queries using Azure OpenAI to check for:
- Syntax errors
- Schema compliance
- Best practices

Uses Semantic Kernel for Azure OpenAI integration.
"""

import os
import sys
import json
import asyncio
import concurrent.futures
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass
from dotenv import load_dotenv

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.contents.chat_history import ChatHistory
from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.azure_chat_prompt_execution_settings import (
    AzureChatPromptExecutionSettings,
)

from backend.tools.auth import AzureOpenAIConfig, AzureOpenAIAuthProvider, create_chat_service, get_llm_provider

# Dynamic prompt loading to avoid caching issues
_project_root = Path(__file__).resolve().parent.parent.parent


def get_dax_validator_prompt() -> str:
    """
    Get the DAX validator prompt.
    Loads fresh from disk each time to ensure latest changes are used.
    """
    import importlib.util
    filepath = _project_root / "backend" / "prompts" / "prompt_validator" / "dax_validator_global_instructions.py"
    spec = importlib.util.spec_from_file_location(
        "backend.prompts.prompt_validator.dax_validator_global_instructions",
        filepath,
        submodule_search_locations=[],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "backend.prompts.prompt_validator"
    spec.loader.exec_module(module)
    return getattr(module, "DAX_VALIDATOR_PROMPT")


load_dotenv()


@dataclass
class ChartMetadata:
    """Metadata for chart generation extracted during validation."""
    metric_name: Optional[str] = None
    dimension: Optional[str] = None
    dimension_type: str = "none"  # 'date', 'categorical', or 'none'


@dataclass
class ValidationResult:
    """Result from DAX validation."""
    success: bool = True  # Add this field
    is_valid: bool = False
    corrected_query: Optional[str] = None
    issues: Optional[list] = None
    error: Optional[str] = None
    chart_metadata: Optional[ChartMetadata] = None


class DAXValidator:
    """Validates DAX queries using the configured LLM provider."""
    
    def __init__(self, config: AzureOpenAIConfig = None):
        self.config = config or AzureOpenAIConfig()
        self._provider_name = get_llm_provider()
        self.kernel = Kernel()
        
        # Create chat service using factory (Azure or Compass)
        self._chat_service, self._settings_class, provider = create_chat_service(service_id="dax_validator")
        self.kernel.add_service(self._chat_service)
        
        print(f"[OK] DAX Validator initialized (provider: {provider})")
    
    async def _validate_async(self, dax_query: str, schema: str, user_query: str, intent: str = "TRANSACTIONS") -> ValidationResult:
        """Async implementation of DAX validation."""
        # Load the global validator prompt fresh from disk
        validator_prompt = get_dax_validator_prompt()
        
        # Format prompt - use 'generated_dax' to match prompt placeholder
        prompt = validator_prompt.format(
            user_query=user_query,
            generated_dax=dax_query,  # Must be 'generated_dax' to match prompt
            schema=schema[:20000] if len(schema) > 20000 else schema
        )
        
        chat_history = ChatHistory(system_message="You are a DAX query validator. Validate queries and suggest corrections if needed. Always respond with valid JSON.")
        chat_history.add_user_message(prompt)
        
        # Use provider-appropriate settings
        if self._provider_name == 'compass':
            settings = self._settings_class(
                max_completion_tokens=2000,
                temperature=0.0,
            )
        else:
            settings = self._settings_class(
                max_completion_tokens=2000,
                extra_body={"reasoning_effort": "low"},
            )
        
        try:
            response = await self._chat_service.get_chat_message_content(
                chat_history=chat_history,
                settings=settings,
            )
            return self._parse_response(str(response))
        except Exception as e:
            return ValidationResult(success=False, is_valid=False, error=str(e))
    
    def _parse_response(self, raw_response: str) -> ValidationResult:
        """Parse LLM response into ValidationResult."""
        try:
            clean_response = raw_response.strip()
            if clean_response.startswith("```"):
                lines = clean_response.split("\n")[1:-1]
                clean_response = "\n".join(lines)
            
            parsed = json.loads(clean_response)
            
            # Parse chart_metadata if present
            chart_metadata = None
            if "chart_metadata" in parsed:
                meta = parsed["chart_metadata"]
                chart_metadata = ChartMetadata(
                    metric_name=meta.get("metric_name"),
                    dimension=meta.get("dimension"),
                    dimension_type=meta.get("dimension_type", "none"),
                )
            
            return ValidationResult(
                success=True,
                is_valid=parsed.get("is_valid", False),
                corrected_query=parsed.get("corrected_dax"),
                issues=parsed.get("issues", []),
                chart_metadata=chart_metadata,
            )
        except json.JSONDecodeError as e:
            return ValidationResult(success=False, is_valid=False, error=f"Failed to parse response: {e}")
    
    def validate(self, dax_query: str, schema: str, user_query: str, intent: str = "TRANSACTIONS") -> ValidationResult:
        """Validate DAX query (sync wrapper)."""
        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._validate_async(dax_query, schema, user_query, intent))
            finally:
                loop.close()
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_thread)
            return future.result(timeout=60)


# Singleton instance for tool use
_validator_instance = None


def get_validator() -> DAXValidator:
    """Get or create singleton validator instance."""
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = DAXValidator()
    return _validator_instance


def validate_dax_query(user_query: str, generated_dax: str, schema: str, intent: str = "TRANSACTIONS") -> str:
    """
    Validate DAX query - Tool function for agent.
    
    Args:
        user_query: The original user question
        generated_dax: The DAX query to validate
        schema: The schema used for generation
        intent: The detected intent ("TRANSACTIONS" or "FEEDBACK")
        
    Returns:
        JSON string with validation results
    """
    import json
    validator = get_validator()
    result = validator.validate(generated_dax, schema, user_query, intent=intent)
    
    return json.dumps({
        "is_valid": result.is_valid,
        "corrected_query": result.corrected_query,
        "issues": result.issues,
        "error": result.error,
    }, indent=2)