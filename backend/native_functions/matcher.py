# backend/native_functions/matcher.py
"""
Native Function Matcher — determines if a user query can be served by a
native function and extracts the required parameters.

Uses Azure OpenAI (same infrastructure as the rest of the pipeline) to:
  1. Decide whether the query matches any registered native function
  2. Extract parameter values from the natural language query

If no match, returns None and the pipeline falls through to normal LLM DAX generation.
"""

import os
import sys
import json
import asyncio
import concurrent.futures
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from dotenv import load_dotenv

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from semantic_kernel import Kernel
from semantic_kernel.contents.chat_history import ChatHistory
from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.azure_chat_prompt_execution_settings import (
    AzureChatPromptExecutionSettings,
)
from backend.tools.auth import create_chat_service, get_llm_provider
from backend.native_functions.registry import NativeFunction, NATIVE_FUNCTIONS

load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class NativeMatchResult:
    """Result of native function matching."""
    matched: bool
    function_name: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    dax_query: Optional[str] = None
    notes: Optional[str] = None
    used_tables: Optional[List[str]] = None
    used_columns: Optional[List[str]] = None
    used_measures: Optional[List[str]] = None
    elapsed: float = 0.0


# ============================================================
# LLM Matching Prompt
# ============================================================

NATIVE_MATCHER_SYSTEM_PROMPT = """You are a query classifier for a DAX analytics system.

Your job: given a user question and a catalog of pre-built native functions, determine if the question can be answered by one of the native functions. If yes, extract the required parameters.

## Available Native Functions

{functions_catalog}

## Matching Rules

1. MATCH if the user's question is asking about a metric or analysis that a native function covers. The user does NOT need to use the exact same words. Focus on INTENT, not exact phrasing.
2. The example questions show the KINDS of queries that should match. Any question with similar intent should also match.
3. DO NOT match only if the question requires something fundamentally different from what any native function provides (e.g., a completely different metric, a custom calculation, or a join/comparison not in the template).
4. When the user asks about a metric (NPS, CES, CSAT) and a native function exists for that metric, MATCH IT. The native function's optional parameters handle variations like entity filtering and time periods.
5. Extract parameter values from the user's question. Use defaults when the user doesn't specify a value.
6. For entity/ADGE names, use the short code format (e.g., "DOH" for Department of Health, "DED" for Department of Economic Development).
7. When the user says "last N months", map to n_months parameter.
8. When the user doesn't specify an entity, leave entity_filter as empty string.
9. When the user doesn't specify a time period for non-trend queries, use defaults (year=0, month=0 means no filter).
10. When the user mentions a specific month like "January 2025", extract year=2025 and month=1.

## IMPORTANT

Your default should be to MATCH. Only return match=false when you are certain that NO native function can serve the question. If a native function covers the right metric even if the exact grouping or filters differ slightly, MATCH IT.

## Output Format

Return ONLY valid JSON -- no markdown, no explanation.

If a native function matches:
{{
  "match": true,
  "function_name": "<name>",
  "parameters": {{ "<param>": <value>, ... }}
}}

If no native function matches:
{{
  "match": false
}}
"""


def _build_functions_catalog(domain: Optional[str] = None) -> str:
    """Build a human-readable catalog of native functions for the LLM prompt."""
    functions = NATIVE_FUNCTIONS
    if domain:
        functions = [f for f in functions if f.domain == domain]

    if not functions:
        return "(No native functions available for this domain)"

    lines = []
    for func in functions:
        lines.append(f"### {func.name}")
        lines.append(f"  Domain: {func.domain}")
        lines.append(f"  Description: {func.description}")
        if func.examples:
            lines.append(f"  Example questions that should match:")
            for ex in func.examples:
                lines.append(f"    - \"{ex}\"")
        lines.append(f"  Parameters:")
        for p in func.parameters:
            req = "required" if p.required else f"optional, default={p.default}"
            lines.append(f"    - {p.name} ({p.type}, {req}): {p.description}")
            if p.enum:
                lines.append(f"      Allowed values: {', '.join(p.enum)}")
        lines.append("")
    return "\n".join(lines)


def _get_function_by_name(name: str) -> Optional[NativeFunction]:
    """Look up a native function by name."""
    for f in NATIVE_FUNCTIONS:
        if f.name == name:
            return f
    return None


# ============================================================
# DAX template post-processing
# ============================================================

def _build_filter_fragments(func: NativeFunction, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build dynamic DAX filter fragments based on parameter values.

    This handles the conditional filter logic that the simple .format()
    cannot do (e.g., region filter is only included when a value is provided).
    """
    fragments = dict(params)

    # Region filter
    region = params.get("region_filter", "")
    if region:
        fragments["region_filter_var"] = (
            f"VAR __RegionFilter =\n"
            f"        FILTER(\n"
            f"            ALL('dim_region'[region_name]),\n"
            f"            'dim_region'[region_name] = \"{region}\"\n"
            f"        )"
        )
        fragments["region_filter_ref"] = "__RegionFilter,"
    else:
        fragments["region_filter_var"] = "// No region filter applied"
        fragments["region_filter_ref"] = ""

    # Entity filter (for new model - dim_entity)
    entity = params.get("entity_filter", "")
    entity_table = params.get("entity_table", "dim_entity")
    entity_column = params.get("entity_column", "entity_name")
    if entity:
        fragments["entity_filter_var"] = (
            f"VAR __EntityFilter =\n"
            f"        FILTER(\n"
            f"            ALL('{entity_table}'[{entity_column}]),\n"
            f"            '{entity_table}'[{entity_column}] = \"{entity}\"\n"
            f"        )"
        )
        fragments["entity_filter_ref"] = "__EntityFilter,"
    else:
        fragments["entity_filter_var"] = "// No entity filter applied"
        fragments["entity_filter_ref"] = ""

    # Date filter (year/quarter/month)
    year = params.get("year", 0)
    quarter = params.get("quarter", 0)
    month = params.get("month", 0)
    if year and quarter:
        fragments["date_filter_var"] = (
            f"VAR __DateFilter =\n"
            f"        FILTER(\n"
            f"            ALL('dim_date'),\n"
            f"            'dim_date'[year] = {year} && 'dim_date'[quarter] = {quarter}\n"
            f"        )"
        )
        fragments["date_filter_ref"] = "__DateFilter,"
    elif year and month:
        fragments["date_filter_var"] = (
            f"VAR __DateFilter =\n"
            f"        FILTER(\n"
            f"            ALL('dim_date'),\n"
            f"            'dim_date'[year] = {year} && 'dim_date'[month_number] = {month}\n"
            f"        )"
        )
        fragments["date_filter_ref"] = "__DateFilter,"
    elif year:
        fragments["date_filter_var"] = (
            f"VAR __DateFilter =\n"
            f"        FILTER(\n"
            f"            ALL('dim_date'),\n"
            f"            'dim_date'[year] = {year}\n"
            f"        )"
        )
        fragments["date_filter_ref"] = "__DateFilter,"
    else:
        fragments["date_filter_var"] = "// No date filter applied"
        fragments["date_filter_ref"] = ""

    # Impact levels formatting (for downtime_high_public_impact)
    impact_levels = params.get("impact_levels", "")
    if impact_levels:
        levels = [l.strip() for l in impact_levels.split(",")]
        fragments["impact_levels_formatted"] = ", ".join(f'"{l}"' for l in levels)
    else:
        fragments["impact_levels_formatted"] = '"High", "Critical"'

    return fragments


# ============================================================
# LLM Matching
# ============================================================

_shared_chat_service = None
_shared_settings_class = None


def _get_chat_service():
    """Get or create a shared chat service for matching."""
    global _shared_chat_service, _shared_settings_class
    if _shared_chat_service is None:
        _shared_chat_service, _shared_settings_class, _ = create_chat_service()
    return _shared_chat_service


async def _match_async(
    user_query: str,
    domain: Optional[str] = None,
) -> NativeMatchResult:
    """
    Use LLM to determine if the user query matches a native function.
    """
    start = time.time()

    # Build the catalog with ALL native functions (don't filter by domain —
    # the LLM decides intent, and native DAX templates are self-contained)
    catalog = _build_functions_catalog(None)
    func_count = len(NATIVE_FUNCTIONS)
    print(f"[NATIVE MATCHER] domain={domain}, {func_count} function(s) in catalog (all domains)")
    system_prompt = NATIVE_MATCHER_SYSTEM_PROMPT.format(functions_catalog=catalog)

    # Set up the LLM call
    chat_service = _get_chat_service()
    history = ChatHistory()
    history.add_system_message(system_prompt)
    history.add_user_message(user_query)

    print(f"[NATIVE MATCHER] User query: {user_query}")
    print(f"[NATIVE MATCHER] System prompt length: {len(system_prompt)} chars")

    provider = get_llm_provider()
    settings = AzureChatPromptExecutionSettings()
    settings.temperature = 0.0
    settings.extra_body = {"max_completion_tokens": 500}

    try:
        response = await chat_service.get_chat_message_content(
            chat_history=history,
            settings=settings,
        )
        raw = str(response).strip()
        print(f"[NATIVE MATCHER] LLM response: {raw[:300]}")
    except Exception as e:
        print(f"[NATIVE MATCHER] LLM call failed: {e}")
        return NativeMatchResult(matched=False, elapsed=time.time() - start)

    # Parse the response
    try:
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[NATIVE MATCHER] Invalid JSON from LLM: {raw[:300]}")
        return NativeMatchResult(matched=False, elapsed=time.time() - start)

    if not data.get("match"):
        print(f"[NATIVE MATCHER] No match (LLM returned match=false)")
        return NativeMatchResult(matched=False, elapsed=time.time() - start)

    func_name = data.get("function_name")
    params = data.get("parameters", {})
    print(f"[NATIVE MATCHER] Match found: {func_name}, params={params}")

    func = _get_function_by_name(func_name)
    if func is None:
        print(f"[NATIVE MATCHER] Unknown function name: {func_name}")
        return NativeMatchResult(matched=False, elapsed=time.time() - start)

    elapsed = time.time() - start
    return NativeMatchResult(
        matched=True,
        function_name=func_name,
        parameters=params,
        notes=func.notes,
        used_tables=func.used_tables,
        used_columns=func.used_columns,
        used_measures=func.used_measures,
        elapsed=elapsed,
    )


def match_native_function(
    user_query: str,
    domain: Optional[str] = None,
    timeout: int = 15,
) -> NativeMatchResult:
    """
    Synchronous entry point: check if user query matches a native function.

    Args:
        user_query: The user's natural language question.
        domain: Optional domain to restrict matching to.
        timeout: Max seconds to wait for LLM response.

    Returns:
        NativeMatchResult with matched=True if a function was found,
        matched=False otherwise.
    """
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_match_async(user_query, domain))
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(_run)
            return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        print("[NATIVE MATCHER] Timed out")
        return NativeMatchResult(matched=False)
    except Exception as e:
        print(f"[NATIVE MATCHER] Error: {e}")
        import traceback
        traceback.print_exc()
        return NativeMatchResult(matched=False)


def resolve_native_function(match_result: NativeMatchResult) -> Optional[Dict[str, Any]]:
    """
    Given a successful match result, render the final DAX query.

    Args:
        match_result: A NativeMatchResult with matched=True.

    Returns:
        Dict with 'dax_query', 'notes', 'used_tables', 'used_columns', 'used_measures',
        or None if rendering fails.
    """
    if not match_result.matched or not match_result.function_name:
        return None

    func = _get_function_by_name(match_result.function_name)
    if func is None:
        return None

    params = match_result.parameters or {}

    try:
        # Build dynamic filter fragments
        fragments = _build_filter_fragments(func, params)

        # Render the template
        dax_query = func.dax_template.format(**fragments)

        return {
            "dax_query": dax_query,
            "notes": func.notes,
            "used_tables": func.used_tables or [],
            "used_columns": func.used_columns or [],
            "used_measures": func.used_measures or [],
            "native_function": func.name,
            "parameters": params,
        }
    except (KeyError, ValueError) as e:
        logger.error(f"Failed to render native function '{func.name}': {e}")
        return None
