"""
Agent Workflow — LLM Planner + Deterministic Executor

Architecture:
  1. LLM Query Planner  — decomposes user question into domain-specific sub-queries
  2. Plan Executor       — runs each sub-query through the analyst workflow, chaining results
  3. Formatter + Chart   — LLM formatting + visualization

Analyst workflow (unchanged):
  GenerateDAX → ExecuteDAX ↔ ValidateDAX

Exports:
  - create_dax_workflow()  — creates the analyst workflow + shared instances
  - run_pipeline_sync()    — single entry-point for app.py
  - DAXAgentConfig         — configuration class
"""

import os
import sys
import json
import warnings
import logging
import asyncio
import time
import concurrent.futures
import traceback
from pathlib import Path
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

# ============================================
# Suppress httpx event loop errors
# ============================================
warnings.filterwarnings("ignore", message=".*Event loop is closed.*")
warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("asyncio").setLevel(logging.ERROR)

def _custom_exception_handler(loop, context):
    exception = context.get('exception')
    if exception and 'Event loop is closed' in str(exception):
        return
    if exception:
        logging.error(f"Async exception: {exception}")

try:
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(_custom_exception_handler)
except RuntimeError:
    pass

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()

# Agent Framework imports (only the workflow/executor parts)
from agent_framework import WorkflowBuilder, WorkflowOutputEvent, WorkflowContext, Case, Default, Executor, handler

# Import executors (only the 3 used by analyst workflow)
from backend.executors import (
    GenerateDAXExecutor,
    ValidateDAXExecutor,
    ExecuteDAXExecutor,
)
from backend.executors.workflow_state import (
    get_workflow_state,
    set_workflow_state,
    reset_workflow_state,
    DAXWorkflowState,
    ConversationTurn,
)

# Import tools for shared instances
from backend.tools.generate_dax import DAXGenerator
from backend.tools.validate_dax import DAXValidator
from backend.tools.execute_dax import get_executor
from backend.tools.format_dax_results import get_formatter
from backend.tools.chart_visualizer import (
    get_visualizer,
    ChartMetadata,
    extract_chart_metadata_from_dax,
)
from backend.tools.query_planner import get_planner, ExecutionPlan, PlanStep

# Import timing
from backend.utils.timing import PipelineTiming

# Import prompts
from schema_extraction.domain_configs import DOMAIN_REGISTRY

# Import native functions
from backend.native_functions.matcher import match_native_function, resolve_native_function

logger = logging.getLogger(__name__)


# ============================================================
# Configuration
# ============================================================

class DAXAgentConfig:
    """Configuration for the DAX agent."""

    def __init__(self):
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
        self.tenant_id = os.getenv("TENANT_ID")
        self.client_id = os.getenv("CLIENT_ID_OPENAI")
        self.client_secret = os.getenv("CLIENT_SECRET_OPENAI")

    def validate(self):
        """Validate required configuration."""
        required = {
            "AZURE_OPENAI_ENDPOINT": self.endpoint,
            "AZURE_OPENAI_DEPLOYMENT": self.deployment_name,
            "TENANT_ID": self.tenant_id,
            "CLIENT_ID_OPENAI": self.client_id,
            "CLIENT_SECRET_OPENAI": self.client_secret,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(f"Missing environment variables: {', '.join(missing)}")


# ============================================================
# 3-Step Analyst Workflow (GenerateDAX → ExecuteDAX ↔ ValidateDAX)
# ============================================================

def create_dax_workflow(pre_connect_powerbi: bool = True):
    """
    Create the 3-step analyst workflow (no extract_intent, no format_results).

    The workflow:
        GenerateDAX → ExecuteDAX ─success─→ END (emit output)
                          │
                          └─fail──→ ValidateDAX → ExecuteDAX (retry)

    Returns:
        tuple: (workflow, shared_instances dict)
    """
    print("[INIT] Creating 3-step Analyst Workflow...")

    # Pre-initialize shared instances
    shared = {
        "query_planner": get_planner(),
        "dax_generator": DAXGenerator(),
        "dax_validator": DAXValidator(),
        "results_formatter": get_formatter(),
        "chart_visualizer": get_visualizer(),
        "dax_executor": get_executor(),
        "conversation_history": [],  # Persistent across requests for follow-up context
    }

    # Eagerly initialize the formatter's LLM connection so the first query
    # doesn't pay a cold-start penalty (connection setup + TLS handshake).
    try:
        shared["results_formatter"]._ensure_initialized()
    except Exception as e:
        print(f"[WARN] Formatter pre-init failed (will lazy-init on first use): {e}")

    print("[PERF] Shared instances ready")

    # Pre-connect to Power BI if requested
    if pre_connect_powerbi:
        print("[PERF] Pre-connecting to Power BI XMLA endpoint...")
        try:
            shared["dax_executor"]._ensure_connection()
            print("[PERF] Power BI connection established")
        except Exception as e:
            print(f"[WARN] Could not pre-connect: {e}")

    # Create executor nodes
    generate_dax = GenerateDAXExecutor(dax_generator=shared["dax_generator"])
    validate_dax = ValidateDAXExecutor(dax_validator=shared["dax_validator"])
    execute_dax = ExecuteDAXExecutor(dax_executor=shared["dax_executor"])

    # Terminal executor: emits the final result as workflow output
    class WorkflowOutputExecutor(Executor):
        """Simple pass-through executor that yields output and ends the workflow."""
        def __init__(self):
            super().__init__(id="workflow_output")

        @handler
        async def handle_message(self, message: Dict[str, Any], ctx: WorkflowContext[Dict[str, Any]]) -> None:
            await ctx.yield_output(message)

    output_executor = WorkflowOutputExecutor()

    # Build workflow: GenerateDAX → ExecuteDAX ↔ ValidateDAX → Output
    builder = WorkflowBuilder()
    builder.set_start_executor(generate_dax)
    builder.add_edge(generate_dax, execute_dax)
    builder.add_edge(validate_dax, execute_dax)

    # Conditional: on execution failure → validate; on success → output
    builder.add_switch_case_edge_group(
        execute_dax,
        [
            Case(
                condition=lambda message: message.get("phase") == "RETRY_VALIDATE",
                target=validate_dax,
            ),
            Default(target=output_executor),  # Emit result and end workflow
        ],
    )

    workflow = builder.build()

    print("[OK] Analyst workflow: GenerateDAX → ExecuteDAX ↔ ValidateDAX")
    return workflow, shared


def _build_analyst_workflow(shared: Dict[str, Any]):
    """
    Build a lightweight workflow graph from existing shared executors.
    Cheap to call — no LLM client re-init, just graph wiring.
    Used for parallel cross-domain execution (each run needs its own workflow instance).
    """
    generate_dax = GenerateDAXExecutor(dax_generator=shared["dax_generator"])
    validate_dax = ValidateDAXExecutor(dax_validator=shared["dax_validator"])
    execute_dax = ExecuteDAXExecutor(dax_executor=shared["dax_executor"])

    class WorkflowOutputExecutor(Executor):
        def __init__(self):
            super().__init__(id="workflow_output")

        @handler
        async def handle_message(self, message: Dict[str, Any], ctx: WorkflowContext[Dict[str, Any]]) -> None:
            await ctx.yield_output(message)

    output_executor = WorkflowOutputExecutor()

    builder = WorkflowBuilder()
    builder.set_start_executor(generate_dax)
    builder.add_edge(generate_dax, execute_dax)
    builder.add_edge(validate_dax, execute_dax)
    builder.add_switch_case_edge_group(
        execute_dax,
        [
            Case(
                condition=lambda message: message.get("phase") == "RETRY_VALIDATE",
                target=validate_dax,
            ),
            Default(target=output_executor),
        ],
    )
    return builder.build()


# ============================================================
# Run analyst workflow (async + sync wrappers)
# ============================================================

async def run_analyst_workflow(
    workflow,
    user_query: str,
    intent: str,
    schema_content: str,
    access_token: str = None,
    conversation_history: List[ConversationTurn] = None,
) -> Dict[str, Any]:
    """
    Run the 3-step analyst workflow for a query.

    Sets up state with intent + schema (previously done by ExtractIntentExecutor),
    then runs GenerateDAX → ExecuteDAX ↔ ValidateDAX.
    """
    start_time = time.time()

    # Reset state and pre-populate intent + schema
    state = reset_workflow_state()
    state.user_query = user_query
    state.original_user_query = user_query
    state.access_token = access_token
    state.intent = intent
    state.schema_content = schema_content
    state.steps_completed.append("extract_intent")  # Done by router

    # Inject conversation history so GenerateDAXExecutor can use it
    if conversation_history:
        state.conversation_history = conversation_history

    print(f"\n{'='*70}")
    print(f"[ANALYST WORKFLOW] domain={intent}")
    print(f"   Query: {user_query}")
    print(f"{'='*70}")

    final_result = None
    event_count = 0

    # Pass a dict (not a raw string) so GenerateDAXExecutor.handle_message matches
    initial_message = {"success": True, "user_query": user_query, "step": "start"}

    async for event in workflow.run_stream(initial_message):
        event_count += 1
        if isinstance(event, WorkflowOutputEvent):
            final_result = event.data

    elapsed = time.time() - start_time
    print(f"[TIMING] Analyst workflow: {event_count} events in {elapsed:.2f}s")

    # Build result from state
    state = get_workflow_state()

    result = {
        "success": state.execution_success,
        "columns": state.columns,
        "data": state.data,
        "row_count": state.row_count,
        "dax_query": state.final_dax or state.generated_dax,
        "intent": state.intent,
        "error": state.error,
        "steps_completed": state.steps_completed,
        "requires_reauth": state.requires_reauth,
        "elapsed_time": elapsed,
        # Chart metadata from validation step
        "chart_metric_name": state.chart_metric_name,
        "chart_dimension": state.chart_dimension,
        "chart_dimension_type": state.chart_dimension_type or "none",
        # Timing
        "step_timings": state.step_timings,
        "dax_generation_ttft": state.dax_generation_ttft,
        "dax_generation_ttlt": state.dax_generation_ttlt,
    }

    # Merge with final_result if available
    if final_result:
        result["success"] = final_result.get("success", result["success"])
        if final_result.get("error"):
            result["error"] = final_result["error"]

    return result


def run_analyst_workflow_sync(
    workflow,
    user_query: str,
    intent: str,
    schema_content: str,
    access_token: str = None,
    conversation_history: List[ConversationTurn] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Run the analyst workflow synchronously in a separate thread."""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                run_analyst_workflow(workflow, user_query, intent, schema_content, access_token, conversation_history)
            )
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(_run)
            return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return {"success": False, "error": f"Analyst timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": str(e)}




# ============================================================
# LLM Planner → Plan Executor → Format pipeline
# ============================================================

# Module-level storage for shared instances (set by create_dax_workflow)
_global_shared: Dict[str, Any] = {}


def _summarize_result_for_chaining(result: dict, source_domain: str) -> str:
    """
    Summarize an analyst result into concise context for the next analyst.
    Keeps it short to avoid token bloat in the DAX generator prompt.
    """
    if not result.get("success"):
        return f"The {source_domain} analysis did not return results."

    columns = result.get("columns", [])
    data = result.get("data", [])

    if not data:
        return f"The {source_domain} analysis returned no data."

    rows = data[:10]
    lines = [f"Data from {source_domain} analysis (columns: {', '.join(columns)}):"]
    for row in rows:
        if isinstance(row, dict):
            parts = [f"{k}: {v}" for k, v in row.items()]
            lines.append(f"  - {', '.join(parts)}")
        elif isinstance(row, (list, tuple)):
            lines.append(f"  - {', '.join(str(v) for v in row)}")
    if len(data) > 10:
        lines.append(f"  ... ({len(data)} total rows)")

    return "\n".join(lines)


def _run_native_function(shared: Dict, step_query: str, domain: str, access_token: str = None):
    """
    Try to match and execute a native function for the given query.

    Returns a tuple: (result_dict_or_None, match_info_dict).
    match_info is always populated so the pipeline can track the attempt.
    """
    print(f"[NATIVE] Checking for native function match (domain={domain})...")

    match_result = match_native_function(step_query, domain=domain)
    match_info = {
        "attempted": True,
        "matched": match_result.matched,
        "function_name": match_result.function_name,
        "match_elapsed": match_result.elapsed,
    }

    if not match_result.matched:
        print(f"[NATIVE] No match found ({match_result.elapsed:.2f}s)")
        return None, match_info

    print(f"[NATIVE] Matched: {match_result.function_name} "
          f"(params={match_result.parameters}, {match_result.elapsed:.2f}s)")

    # Render the DAX template with extracted parameters
    resolved = resolve_native_function(match_result)
    if resolved is None:
        print(f"[NATIVE] Failed to render template — falling back to LLM generation")
        return None, match_info

    dax_query = resolved["dax_query"]
    print(f"[NATIVE] Rendered DAX:\n{dax_query[:300]}...")

    # Execute the DAX directly using the shared executor
    dax_executor = shared["dax_executor"]
    try:
        exec_result = dax_executor.execute(dax_query, access_token=access_token)

        if not exec_result.success:
            print(f"[NATIVE] Execution failed: {exec_result.error}")
            print(f"[NATIVE] Falling back to LLM generation")
            return None, match_info

        result = {
            "success": True,
            "columns": exec_result.columns,
            "data": exec_result.data,
            "row_count": exec_result.row_count,
            "dax_query": dax_query,
            "intent": domain,
            "error": None,
            "requires_reauth": False,
            "steps_completed": ["native_function", "execute_dax"],
            "native_function": resolved["native_function"],
            "native_params": resolved["parameters"],
            "chart_metric_name": None,
            "chart_dimension": None,
            "chart_dimension_type": "none",
            "user_query": step_query,
            "step_timings": {"native_match": match_result.elapsed},
            "dax_generation_ttft": None,
            "dax_generation_ttlt": None,
            "used_tables": resolved.get("used_tables", []),
            "used_columns": resolved.get("used_columns", []),
            "used_measures": resolved.get("used_measures", []),
        }

        print(f"[NATIVE] Success: {exec_result.row_count} rows returned")
        return result, match_info

    except Exception as e:
        print(f"[NATIVE] Execution error: {e} — falling back to LLM generation")
        return None, match_info


async def _run_pipeline_async(
    shared: Dict[str, Any],
    workflow,
    user_query: str,
    access_token: str = None,
) -> Dict[str, Any]:
    """
    Main pipeline: LLM Planner -> sequential executor -> format + chart.

    1. Call the LLM Query Planner to decompose the user question
    2. Execute each plan step through the analyst workflow
       - Inject prior step results for dependent steps
    3. Merge all results, format with LLM, and generate chart
    """
    timing = PipelineTiming()

    # Store access token in side-channel for analyst calls
    shared["_router_context"] = {
        "analyst_results": [],
        "access_token": access_token,
    }

    # Retrieve persistent conversation history from shared dict
    conversation_history: List[ConversationTurn] = shared.get("conversation_history", [])

    print(f"\n{'='*70}")
    print(f"[PIPELINE] Processing query")
    print(f"   Query: {user_query}")
    print(f"   Conversation history: {len(conversation_history)} prior turn(s)")
    print(f"{'='*70}")

    # -- Step 1: LLM Query Planner ----------------------------
    planner = shared["query_planner"]
    plan = planner.plan(user_query, conversation_history=conversation_history)
    timing.record_planner(plan)

    print(f"[PIPELINE] Plan: {len(plan.steps)} step(s), "
          f"cross_domain={plan.is_cross_domain}, "
          f"has_deps={plan.has_dependencies}, "
          f"elapsed={plan.planner_elapsed:.2f}s")
    for step in plan.steps:
        dep_str = f" (depends_on={step.depends_on})" if step.depends_on else ""
        print(f"   Step {step.id}: [{step.domain}] {step.query[:100]}{dep_str}")

    if plan.error:
        print(f"[PIPELINE] Planner warning: {plan.error}")

    # -- Check for clarification request -----------------------
    if plan.clarification_needed:
        print(f"[PIPELINE] Clarification needed — returning suggestions to user")
        timing.finish()
        return {
            "success": True,
            "clarification_needed": True,
            "clarification_message": plan.clarification_message,
            "clarification_suggestions": plan.clarification_suggestions,
            "formatted_answer": None,
            "chart_config": None,
            "chart_type": "none",
            "steps_completed": ["planner"],
            "row_count": 0,
            "dax_query": None,
            "elapsed_time": timing.total_elapsed,
            "timing": timing.to_dict(),
        }

    # -- Step 2: Execute plan steps sequentially ---------------
    step_results: Dict[int, dict] = {}
    all_analyst_results: List[dict] = []

    for step in plan.steps:
        print(f"\n[PIPELINE] Executing step {step.id}: [{step.domain}] {step.query[:80]}...")

        # Build the query for this step
        step_query = step.query

        # If this step depends on a prior step, inject the prior result as context
        if step.depends_on and step.depends_on in step_results:
            prior = step_results[step.depends_on]
            context = _summarize_result_for_chaining(prior, prior.get("intent", "prior"))
            step_query = (
                f"{step.query}\n\n"
                f"[Context from prior analysis - use these specific values to answer the question:\n"
                f"{context}\n"
                f"Use the entity/service names and values above to filter your query appropriately.]"
            )
            print(f"[PIPELINE]   Injected context from step {step.depends_on} "
                  f"({prior.get('row_count', 0)} rows)")

        # Run the analyst workflow for this step
        timing.start_step(step.id, step.domain)

        # --- Native function fast path ---
        # Check if this step can be served by a parameterized native function
        # (bypasses LLM DAX generation entirely)
        ctx = shared.get("_router_context", {})
        native_result, native_match_info = await asyncio.to_thread(
            _run_native_function, shared, step_query, step.domain,
            ctx.get("access_token"),
        )

        # Record the native match attempt (regardless of outcome)
        timing.record_native_attempt(
            step.id,
            matched=native_match_info.get("matched", False),
            function_name=native_match_info.get("function_name"),
            match_elapsed=native_match_info.get("match_elapsed", 0.0),
        )

        if native_result is not None:
            result = native_result
        else:
            # --- Normal LLM path ---
            wf = _build_analyst_workflow(shared) if len(plan.steps) > 1 else workflow
            result_json = await asyncio.to_thread(
                _run_analyst, wf, shared, step_query, step.domain, conversation_history
            )

            try:
                result = json.loads(result_json)
            except json.JSONDecodeError:
                result = {"success": False, "error": f"Failed to parse {step.domain} result"}

        # End step timing and capture executor sub-timings from the result
        step_elapsed = timing.end_step(
            step.id,
            executor_timings=result.get("step_timings"),
            native_function=result.get("native_function"),
            native_match_time=native_match_info.get("match_elapsed", 0.0),
            native_params=result.get("native_params"),
        )

        result["intent"] = step.domain
        step_results[step.id] = result
        all_analyst_results.append(result)

        native_tag = f" [native:{result['native_function']}]" if result.get("native_function") else ""
        print(f"[PIPELINE]   Step {step.id} [{step.domain}]{native_tag}: {step_elapsed:.2f}s "
              f"(success={result.get('success')}, rows={result.get('row_count', 0)})")

        # Check for auth errors - stop immediately
        if result.get("requires_reauth"):
            timing.finish()
            return {
                "success": False,
                "error": result.get("error", "Authentication required"),
                "formatted_answer": None,
                "steps_completed": result.get("steps_completed", []),
                "requires_reauth": True,
                "elapsed_time": timing.total_elapsed,
                "timing": timing.to_dict(),
            }

        # If a dependent step fails, stop execution
        if not result.get("success") and any(
            s.depends_on == step.id for s in plan.steps
        ):
            print(f"[PIPELINE]   Step {step.id} failed and has dependents - stopping")
            break

    shared["_router_context"]["analyst_results"] = all_analyst_results

    # Determine the primary result (last successful, or the final one)
    primary_result = all_analyst_results[-1] if all_analyst_results else {}
    all_success = all(ar.get("success") for ar in all_analyst_results)

    # -- Step 3: Format results + chart ------------------------
    fmt = {}

    if any(ar.get("success") for ar in all_analyst_results):
        timing.start_format()
        try:
            if len(all_analyst_results) > 1:
                # Cross-domain: format with combined multi-result approach
                fmt = _format_cross_domain_output(shared, all_analyst_results, user_query)
            else:
                # Single-domain: format the only result
                result_to_format = all_analyst_results[0]
                result_to_format["user_query"] = user_query
                fmt = _format_analyst_output(shared, result_to_format)
        except Exception as e:
            print(f"[PIPELINE] Format error: {e}")
            traceback.print_exc()
            fmt = {"success": False, "error": str(e)}
        timing.end_format()
        print(f"[PIPELINE] Format: {timing.format_elapsed:.2f}s")

    # Capture DAX generation streaming metrics from the last analyst result
    last_analyst = all_analyst_results[-1] if all_analyst_results else {}
    timing.dax_generation_ttft = last_analyst.get("dax_generation_ttft")
    timing.dax_generation_ttlt = last_analyst.get("dax_generation_ttlt")

    timing.finish()
    formatted_answer = fmt.get("formatted_answer") or "No results."

    # -- Build timing summary (appended to the chat answer) ----
    all_steps = []
    all_dax_queries = []

    for ar in all_analyst_results:
        all_steps.extend(ar.get("steps_completed", []))
        if ar.get("dax_query"):
            all_dax_queries.append(ar["dax_query"])

    formatted_answer += timing.to_markdown()

    if "format_results" not in all_steps and fmt.get("success"):
        all_steps.append("format_results")

    is_cross_domain = len(all_analyst_results) > 1

    # -- Update conversation history for follow-ups -----------
    if all_success or primary_result.get("success", False):
        last_dax = last_analyst.get("dax_query") or ""
        # Build a concise summary of the results for context
        result_summary_parts = []
        for ar in all_analyst_results:
            if ar.get("success") and ar.get("data"):
                cols = ar.get("columns", [])
                data = ar.get("data", [])
                rows_preview = data[:5]  # Keep up to 5 rows
                for row in rows_preview:
                    if isinstance(row, (list, tuple)):
                        result_summary_parts.append(
                            ", ".join(f"{c}: {v}" for c, v in zip(cols, row))
                        )
        result_summary = "; ".join(result_summary_parts[:5])  # Cap at 5 entries

        turn = ConversationTurn(
            user_query=user_query,
            dax_query=last_dax,
            result_summary=result_summary or None,
            intent=", ".join(ar.get("intent", "") for ar in all_analyst_results),
        )
        conversation_history.append(turn)
        # Keep only last 10 turns to avoid unbounded growth
        if len(conversation_history) > 10:
            conversation_history[:] = conversation_history[-10:]
        shared["conversation_history"] = conversation_history
        print(f"[PIPELINE] Conversation history updated ({len(conversation_history)} turns)")

    print(f"\n{'='*70}")
    print(f"[PIPELINE] Complete in {timing.total_elapsed:.2f}s")
    print(f"   Planner: {timing.planner_elapsed:.2f}s ({timing.planner_steps_count} steps)")
    print(f"   Analyst calls: {len(all_analyst_results)}{'  (cross-domain)' if is_cross_domain else ''}")
    print(f"   Format: {timing.format_elapsed:.2f}s")
    print(f"   Success: {all_success}")
    print(f"{'='*70}\n")

    return {
        "success": all_success or primary_result.get("success", False),
        "formatted_answer": formatted_answer,
        "chart_config": fmt.get("chart_config"),
        "chart_type": fmt.get("chart_type", "none"),
        "steps_completed": list(dict.fromkeys(all_steps)),
        "row_count": sum(ar.get("row_count", 0) for ar in all_analyst_results),
        "dax_query": (" -> ".join(all_dax_queries) if is_cross_domain
                       else last_analyst.get("dax_query")),
        "elapsed_time": timing.total_elapsed,
        "requires_reauth": any(ar.get("requires_reauth") for ar in all_analyst_results),
        "error": ("; ".join(ar.get("error") for ar in all_analyst_results if ar.get("error"))
                  or None),
        "timing": timing.to_dict(),
        "dax_generation_ttft": timing.dax_generation_ttft,
        "dax_generation_ttlt": timing.dax_generation_ttlt,
    }


def run_pipeline_sync(
    shared: Dict[str, Any],
    workflow,
    user_query: str,
    access_token: str = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Synchronous entry point for the full pipeline.

    Called by app.py:
        run_pipeline_sync(shared=..., workflow=..., user_query=..., access_token=...)
    """
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _run_pipeline_async(shared, workflow, user_query, access_token)
            )
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(_run)
            return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return {"success": False, "error": f"Pipeline timed out after {timeout}s"}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


# ============================================================
# Internal helpers
# ============================================================

def _run_analyst(workflow, shared: Dict, user_question: str, intent: str, conversation_history: List[ConversationTurn] = None) -> str:
    """Run an analyst workflow and return JSON result string."""
    # Load schema for this domain
    schema_file = DOMAIN_REGISTRY[intent]["schema_file"]
    schema_path = project_root / schema_file

    try:
        schema_content = schema_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return json.dumps({"success": False, "error": f"Schema file not found: {schema_file}"})

    # Get access token: prefer side-channel (survives across multiple analyst calls),
    # fallback to workflow state
    ctx = shared.get("_router_context", {})
    access_token = ctx.get("access_token")
    if not access_token:
        state = get_workflow_state()
        access_token = state.access_token if state else None

    result = run_analyst_workflow_sync(
        workflow=workflow,
        user_query=user_question,
        intent=intent,
        schema_content=schema_content,
        access_token=access_token,
        conversation_history=conversation_history,
        timeout=120,
    )

    # Serialize data for JSON (handle non-serializable types)
    serializable = {
        "success": result.get("success", False),
        "columns": result.get("columns", []),
        "data": _make_serializable(result.get("data", [])),
        "row_count": result.get("row_count", 0),
        "dax_query": result.get("dax_query"),
        "intent": intent,
        "error": result.get("error"),
        "requires_reauth": result.get("requires_reauth", False),
        "chart_metric_name": result.get("chart_metric_name"),
        "chart_dimension": result.get("chart_dimension"),
        "chart_dimension_type": result.get("chart_dimension_type", "none"),
        "user_query": user_question,
        "step_timings": result.get("step_timings", {}),
        "dax_generation_ttft": result.get("dax_generation_ttft"),
        "dax_generation_ttlt": result.get("dax_generation_ttlt"),
        "steps_completed": result.get("steps_completed", []),
    }

    return json.dumps(serializable, default=str)


def _format_cross_domain_output(
    shared: Dict,
    analyst_results: List[dict],
    user_query: str,
) -> Dict[str, Any]:
    """
    Format cross-domain results by building a combined prompt that describes
    each domain's result set separately, then asking the LLM to produce one
    unified answer.

    This avoids the misaligned-columns problem that occurs when naively
    merging rows from different schemas into one table.
    """
    results_formatter = shared["results_formatter"]

    # Build a combined "results" block that describes each domain separately
    sections: List[str] = []
    all_columns: List[str] = []
    all_data: List[list] = []
    total_rows = 0

    for ar in analyst_results:
        domain = ar.get("intent", "unknown")
        cols = ar.get("columns", [])
        data = ar.get("data", [])
        rows = ar.get("row_count", len(data))

        # Build a small markdown table for this domain
        if cols and data:
            header = "| " + " | ".join(str(c) for c in cols) + " |"
            sep = "|" + "|".join("---" for _ in cols) + "|"
            body_rows = []
            for row in data[:15]:
                body_rows.append("| " + " | ".join(
                    str(v) if v is not None else "" for v in row
                ) + " |")
            table_md = "\n".join([header, sep] + body_rows)
        else:
            table_md = "(no data)"

        sections.append(
            f"**{domain.title()} results** ({rows} row(s)):\n{table_md}"
        )

        all_columns.extend(cols)
        all_data.extend(data)
        total_rows += rows

    combined_results_text = "\n\n".join(sections)

    # Use the LLM formatter with a combined prompt
    combined_dax = " | ".join(
        ar.get("dax_query", "") for ar in analyst_results if ar.get("dax_query")
    )

    format_result = results_formatter.format(
        user_query=user_query,
        dax_query=combined_dax,
        results={
            "columns": ["(cross-domain — see below)"],
            "data": [],
            "row_count": total_rows,
            # Inject the multi-section text so the LLM sees all results
            "_cross_domain_text": combined_results_text,
        },
    )

    # If the default formatter path doesn't handle _cross_domain_text,
    # fall back to a programmatic combined format
    if format_result.success:
        formatted_answer = format_result.formatted
    else:
        formatted_answer = _create_cross_domain_basic_format(
            user_query, analyst_results
        )

    # Chart: use last successful result for chart metadata
    chart_config = None
    chart_type = "none"
    chart_visualizer = shared["chart_visualizer"]
    last_success = next(
        (ar for ar in reversed(analyst_results) if ar.get("success")),
        None,
    )
    if last_success:
        cols = last_success.get("columns", [])
        data = last_success.get("data", [])
        dax = last_success.get("dax_query")
        chart_metadata = None
        if dax and len(data) > 1:
            chart_metadata = extract_chart_metadata_from_dax(
                dax_query=dax, columns=cols, user_query=user_query,
            )
            if chart_metadata and chart_metadata.dimension_type == "none":
                chart_metadata = None
        chart_result = chart_visualizer.create_visualization(
            columns=cols, data=data, user_query=user_query,
            formatted_response=formatted_answer, chart_metadata=chart_metadata,
        )
        if chart_result.success and chart_result.chart_config:
            chart_config = chart_result.chart_config.to_dict()
            chart_type = chart_result.chart_type

    return {
        "success": True,
        "formatted_answer": formatted_answer,
        "chart_config": chart_config,
        "chart_type": chart_type,
    }


def _create_cross_domain_basic_format(
    user_query: str,
    analyst_results: List[dict],
) -> str:
    """Fallback programmatic format for cross-domain results."""
    parts = [f'### Answer\n\nBased on your question: "{user_query}"\n']

    for ar in analyst_results:
        domain = ar.get("intent", "unknown").title()
        cols = ar.get("columns", [])
        data = ar.get("data", [])
        row_count = ar.get("row_count", len(data))

        parts.append(f"### {domain} Results\n")
        if cols and data:
            header = "| " + " | ".join(str(c) for c in cols) + " |"
            sep = "|" + "|".join("---" for _ in cols) + "|"
            rows_md = []
            for row in data[:15]:
                rows_md.append("| " + " | ".join(
                    str(v) if v is not None else "" for v in row
                ) + " |")
            parts.append("\n".join([header, sep] + rows_md))
            if row_count > 15:
                parts.append(f"\n...and {row_count - 15} more rows")
        else:
            parts.append("No data returned.")
        parts.append("")

    parts.append("### Explanation\n")
    total = sum(ar.get("row_count", 0) for ar in analyst_results)
    parts.append(
        f"- Combined query returned {total} row(s) across "
        f"{len(analyst_results)} domain(s)."
    )

    return "\n\n".join(parts)


def _format_analyst_output(shared: Dict, analyst_output: Dict) -> Dict[str, Any]:
    """Format analyst output using LLM + chart visualizer."""
    user_query = analyst_output.get("user_query", "")
    columns = analyst_output.get("columns", [])
    data = analyst_output.get("data", [])
    row_count = analyst_output.get("row_count", 0)
    dax_query = analyst_output.get("dax_query")

    # LLM formatting
    results_formatter = shared["results_formatter"]
    format_result = results_formatter.format(
        user_query=user_query,
        dax_query=dax_query,
        results={"columns": columns, "data": data, "row_count": row_count},
    )

    if format_result.success:
        formatted_answer = format_result.formatted
    else:
        formatted_answer = _create_basic_format(user_query, columns, data, row_count)

    # Chart visualization
    chart_visualizer = shared["chart_visualizer"]
    chart_metadata = None
    dim_type = analyst_output.get("chart_dimension_type", "none")
    if dim_type and dim_type != "none":
        chart_metadata = ChartMetadata(
            metric_name=analyst_output.get("chart_metric_name"),
            dimension=analyst_output.get("chart_dimension"),
            dimension_type=dim_type,
        )
    elif dax_query and len(data) > 1:
        chart_metadata = extract_chart_metadata_from_dax(
            dax_query=dax_query, columns=columns, user_query=user_query,
        )
        if chart_metadata.dimension_type == "none":
            chart_metadata = None

    chart_config = None
    chart_type = "none"
    chart_result = chart_visualizer.create_visualization(
        columns=columns, data=data, user_query=user_query,
        formatted_response=formatted_answer, chart_metadata=chart_metadata,
    )
    if chart_result.success and chart_result.chart_config:
        chart_config = chart_result.chart_config.to_dict()
        chart_type = chart_result.chart_type

    return {
        "success": True,
        "formatted_answer": formatted_answer,
        "chart_config": chart_config,
        "chart_type": chart_type,
    }


def _make_serializable(data):
    """Convert data rows to JSON-serializable types."""
    result = []
    for row in data:
        result.append([str(v) if not isinstance(v, (int, float, bool, str, type(None))) else v for v in row])
    return result


def _create_basic_format(user_query, columns, data, row_count) -> str:
    """Create basic markdown table if LLM formatting fails."""
    lines = []
    if columns:
        lines.append("| " + " | ".join(str(c) for c in columns) + " |")
        lines.append("|" + "|".join("---" for _ in columns) + "|")
        for row in data[:15]:
            lines.append("| " + " | ".join(str(v) for v in row) + " |")
        if row_count > 15:
            lines.append(f"\n...and {row_count - 15} more rows")

    return f"""### Answer

Based on your question: "{user_query}"

### Results

{chr(10).join(lines) if lines else "No results returned."}

### Explanation

- Query returned {row_count} row(s).
"""


def _create_error_response(user_query, error, analyst_result) -> str:
    """Create error response with partial results."""
    steps = analyst_result.get("steps_completed", [])
    dax = analyst_result.get("dax_query")

    response = f"""### Error

The workflow encountered an error: **{error}**

### Steps Completed

{', '.join(steps) if steps else 'None'}
"""
    if dax:
        response += f"""
### Generated DAX (before error)

```dax
{dax}
```
"""
    return response
