"""
Agent Workflow - Multi-Agent DAX Architecture

This module implements a multi-agent architecture:

Router Agent (orchestrator):
  - extract_intent: determines domain (transactions / feedback)
  - run_transactions_analyst: delegates to Transactions Analyst Agent
  - run_feedback_analyst: delegates to Feedback Analyst Agent
  - format_results: LLM formatting + chart visualization

Analyst Agents (one per domain):
  - run_dax_workflow: 3-step workflow (GenerateDAX → ExecuteDAX ↔ ValidateDAX)

Exports:
  - create_dax_workflow() - Creates the 3-step analyst workflow
  - run_workflow_sync() - Runs the full pipeline synchronously
  - create_dax_agent() - Creates the Router Agent with analyst sub-agents
  - DAXAgentConfig - Configuration class
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
from typing import Dict, Any, Optional
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

# Agent Framework imports
from agent_framework import ChatAgent, WorkflowBuilder, WorkflowOutputEvent, Case, Default
from agent_framework.azure import AzureOpenAIChatClient
from azure.identity import ClientSecretCredential

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
)

# Import tools for shared instances
from backend.tools.extract_intent import IntentExtractor
from backend.tools.generate_dax import DAXGenerator
from backend.tools.validate_dax import DAXValidator
from backend.tools.execute_dax import get_executor
from backend.tools.format_dax_results import get_formatter
from backend.tools.chart_visualizer import (
    get_visualizer,
    ChartMetadata,
    extract_chart_metadata_from_dax,
)

# Import prompts
from backend.prompts.router_agent_prompt import ROUTER_AGENT_PROMPT
from backend.prompts.analyst_agent_prompt import get_analyst_prompt

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
# Domain Registry — add new domains here
# ============================================================

DOMAIN_REGISTRY: Dict[str, Dict[str, str]] = {
    "transactions": {
        "description": "Services, applications, SLA, completion time, status",
        "schema_file": "cache/schema/schema_transactions.txt",
    },
    "feedback": {
        "description": "NPS, CES, CSAT, satisfaction, promoters, detractors",
        "schema_file": "cache/schema/schema_feedback.txt",
    },
    # To add a new domain:
    # "new_domain": {
    #     "description": "...",
    #     "schema_file": "cache/schema/schema_new_domain.txt",
    # },
}


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
        "intent_extractor": IntentExtractor(),
        "dax_generator": DAXGenerator(),
        "dax_validator": DAXValidator(),
        "results_formatter": get_formatter(),
        "chart_visualizer": get_visualizer(),
        "dax_executor": get_executor(),
    }
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

    # Build workflow: GenerateDAX → ExecuteDAX ↔ ValidateDAX
    builder = WorkflowBuilder()
    builder.set_start_executor(generate_dax)
    builder.add_edge(generate_dax, execute_dax)
    builder.add_edge(validate_dax, execute_dax)

    # Conditional: on execution failure → validate; otherwise end
    builder.add_switch_case_edge_group(
        execute_dax,
        [
            Case(
                condition=lambda message: message.get("phase") == "RETRY_VALIDATE",
                target=validate_dax,
            ),
            Default(target=None),  # End of workflow
        ],
    )

    workflow = builder.build()

    print("[OK] Analyst workflow: GenerateDAX → ExecuteDAX ↔ ValidateDAX")
    return workflow, shared


# ============================================================
# Run analyst workflow (async + sync wrappers)
# ============================================================

async def run_analyst_workflow(
    workflow,
    user_query: str,
    intent: str,
    schema_content: str,
    access_token: str = None,
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

    print(f"\n{'='*70}")
    print(f"[ANALYST WORKFLOW] domain={intent}")
    print(f"   Query: {user_query}")
    print(f"{'='*70}")

    final_result = None
    event_count = 0

    async for event in workflow.run_stream(user_query):
        event_count += 1
        if isinstance(event, WorkflowOutputEvent):
            final_result = event.output

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
    timeout: int = 120,
) -> Dict[str, Any]:
    """Run the analyst workflow synchronously in a separate thread."""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                run_analyst_workflow(workflow, user_query, intent, schema_content, access_token)
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
# Full pipeline: Router orchestrates extract_intent → analyst → format_results
# ============================================================

def run_full_pipeline(
    shared: Dict[str, Any],
    workflow,
    user_query: str,
    access_token: str = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Run the full multi-agent pipeline (deterministic, no LLM routing).

    1. extract_intent (keyword-based, no LLM)
    2. analyst workflow (3-step: generate → execute ↔ validate)
    3. format_results (LLM formatting + chart)

    Returns:
        Dict compatible with app.py's QueryResponse
    """
    pipeline_start = time.time()
    step_timings = {}

    print(f"\n{'='*70}")
    print(f"[ROUTER] Starting pipeline")
    print(f"   Query: {user_query}")
    print(f"{'='*70}")

    # ── Step 1: Extract intent ──────────────────────────────
    t0 = time.time()
    intent_extractor = shared["intent_extractor"]
    intent_result = intent_extractor.extract(user_query)
    step_timings["extract_intent"] = time.time() - t0

    if not intent_result.success:
        return {
            "success": False,
            "error": f"Intent extraction failed: {intent_result.error}",
            "formatted_answer": None,
            "steps_completed": [],
        }

    intent = intent_result.intent  # "transactions", "feedback", or "unknown"
    schema_content = intent_result.extracted_schema

    # Default unknown → transactions
    if intent == "unknown":
        intent = "transactions"
        print(f"[ROUTER] Unknown intent, defaulting to TRANSACTIONS")

    print(f"[ROUTER] Intent: {intent.upper()} (confidence: {intent_result.confidence:.0%})")
    print(f"[ROUTER] Keywords: {', '.join(intent_result.matched_keywords[:5])}")

    # ── Step 2: Run analyst workflow ────────────────────────
    t0 = time.time()
    analyst_result = run_analyst_workflow_sync(
        workflow=workflow,
        user_query=user_query,
        intent=intent,
        schema_content=schema_content,
        access_token=access_token,
        timeout=timeout,
    )
    analyst_elapsed = time.time() - t0

    # Merge analyst step timings
    if analyst_result.get("step_timings"):
        step_timings.update(analyst_result["step_timings"])

    # Check for re-auth
    if analyst_result.get("requires_reauth"):
        return {
            "success": False,
            "error": analyst_result.get("error", "Authentication required"),
            "formatted_answer": None,
            "steps_completed": analyst_result.get("steps_completed", []),
            "requires_reauth": True,
        }

    # Check for failure
    if not analyst_result.get("success"):
        error = analyst_result.get("error", "Analyst workflow failed")
        return {
            "success": False,
            "error": error,
            "formatted_answer": _create_error_response(user_query, error, analyst_result),
            "steps_completed": analyst_result.get("steps_completed", []),
            "dax_query": analyst_result.get("dax_query"),
            "chart_config": None,
            "chart_type": "none",
        }

    # ── Step 3: Format results + chart ──────────────────────
    t0 = time.time()

    columns = analyst_result.get("columns", [])
    data = analyst_result.get("data", [])
    row_count = analyst_result.get("row_count", 0)
    dax_query = analyst_result.get("dax_query")

    # 3a. LLM formatting
    results_formatter = shared["results_formatter"]
    format_result = results_formatter.format(
        user_query=user_query,
        dax_query=dax_query,
        results={"columns": columns, "data": data, "row_count": row_count},
    )

    if format_result.success:
        formatted_answer = format_result.formatted
    else:
        print(f"[WARN] LLM formatting failed: {format_result.error}, using basic format")
        formatted_answer = _create_basic_format(user_query, columns, data, row_count)

    # 3b. Chart visualization
    chart_visualizer = shared["chart_visualizer"]

    chart_metadata = None
    dim_type = analyst_result.get("chart_dimension_type", "none")
    if dim_type and dim_type != "none":
        chart_metadata = ChartMetadata(
            metric_name=analyst_result.get("chart_metric_name"),
            dimension=analyst_result.get("chart_dimension"),
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
        columns=columns,
        data=data,
        user_query=user_query,
        formatted_response=formatted_answer,
        chart_metadata=chart_metadata,
    )
    if chart_result.success and chart_result.chart_config:
        chart_config = chart_result.chart_config.to_dict()
        chart_type = chart_result.chart_type
        print(f"[ROUTER] Chart: {chart_type} - {chart_result.chart_config.title}")

    step_timings["format_results"] = time.time() - t0

    # ── Build timing summary ────────────────────────────────
    total_elapsed = time.time() - pipeline_start
    timing_text = "\n\n---\n**⏱️ Execution Timing:**\n"
    for step_name, step_time in step_timings.items():
        timing_text += f"- {step_name}: {step_time:.2f}s\n"
        if step_name == "generate_dax":
            ttft = analyst_result.get("dax_generation_ttft")
            ttlt = analyst_result.get("dax_generation_ttlt")
            if ttft and ttlt:
                gen_time = ttlt - ttft
                timing_text += f"  - TTFT: {ttft:.2f}s | Gen: {gen_time:.2f}s | TTLT: {ttlt:.2f}s\n"
    timing_text += f"- **Total**: {total_elapsed:.2f}s"
    formatted_answer += timing_text

    steps_completed = analyst_result.get("steps_completed", [])
    if "format_results" not in steps_completed:
        steps_completed.append("format_results")

    print(f"\n{'='*70}")
    print(f"[ROUTER] Pipeline complete in {total_elapsed:.2f}s")
    print(f"   Steps: {' → '.join(steps_completed)}")
    print(f"{'='*70}\n")

    return {
        "success": True,
        "formatted_answer": formatted_answer,
        "chart_config": chart_config,
        "chart_type": chart_type,
        "steps_completed": steps_completed,
        "row_count": row_count,
        "dax_query": dax_query,
        "elapsed_time": total_elapsed,
        "requires_reauth": False,
        "dax_generation_ttft": analyst_result.get("dax_generation_ttft"),
        "dax_generation_ttlt": analyst_result.get("dax_generation_ttlt"),
    }


# Module-level storage for shared instances (set by create_dax_workflow)
_global_shared: Dict[str, Any] = {}


# ============================================================
# Backward-compatible run_workflow_sync (called by app.py)
# ============================================================

def run_workflow_sync(workflow, user_query: str, timeout: int = 120, access_token: str = None) -> Dict[str, Any]:
    """
    Run the full pipeline synchronously — drop-in replacement for old API.

    app.py calls: run_workflow_sync(workflow=..., user_query=..., timeout=..., access_token=...)
    """
    shared = _global_shared
    if not shared:
        raise RuntimeError("Shared instances not initialized. Call create_dax_workflow() first.")

    return run_full_pipeline(
        shared=shared,
        workflow=workflow,
        user_query=user_query,
        access_token=access_token,
        timeout=timeout,
    )


# ============================================================
# Agent creation (Router + Analyst agents)
# ============================================================

def create_dax_agent(workflow=None, shared_instances=None):
    """
    Create the Router Agent with analyst sub-agents.

    This is the backward-compatible entry point called by app.py.
    The router agent is available for interactive / DevUI usage.
    The deterministic pipeline (run_workflow_sync) is used for the web UI.

    Returns:
        ChatAgent configured as router
    """
    global _global_shared

    print("[INIT] Initializing Multi-Agent Architecture...")

    config = DAXAgentConfig()
    config.validate()

    credential = ClientSecretCredential(
        tenant_id=config.tenant_id,
        client_id=config.client_id,
        client_secret=config.client_secret,
    )

    chat_client = AzureOpenAIChatClient(
        endpoint=config.endpoint,
        credential=credential,
        deployment_name=config.deployment_name,
        api_version=config.api_version,
    )

    if workflow is None:
        workflow, shared_instances = create_dax_workflow()

    _global_shared = shared_instances

    # ── Router Tools ────────────────────────────────────────

    intent_extractor = shared_instances["intent_extractor"]

    def extract_intent(user_question: str) -> str:
        """
        Classify a user question into a domain: transactions or feedback.

        Args:
            user_question: The user's natural language question about data

        Returns:
            JSON with intent, confidence, matched_keywords
        """
        result = intent_extractor.extract(user_question)
        return json.dumps({
            "intent": result.intent,
            "confidence": result.confidence,
            "matched_keywords": result.matched_keywords,
        })

    def run_transactions_analyst(user_question: str) -> str:
        """
        Send a TRANSACTIONS domain question to the Transactions Analyst.
        The analyst generates DAX, executes it against Power BI, and returns raw results.

        Args:
            user_question: A question about transactions, services, SLA, applications

        Returns:
            JSON with columns, data, row_count, dax_query
        """
        return _run_analyst(workflow, shared_instances, user_question, "transactions")

    def run_feedback_analyst(user_question: str) -> str:
        """
        Send a FEEDBACK domain question to the Feedback Analyst.
        The analyst generates DAX, executes it against Power BI, and returns raw results.

        Args:
            user_question: A question about NPS, CES, CSAT, satisfaction, feedback

        Returns:
            JSON with columns, data, row_count, dax_query
        """
        return _run_analyst(workflow, shared_instances, user_question, "feedback")

    def format_results(analyst_output_json: str) -> str:
        """
        Format raw analyst results into a readable answer with charts.
        Call this after receiving results from an analyst agent.

        Args:
            analyst_output_json: JSON string returned by an analyst agent

        Returns:
            Formatted markdown answer with timing and chart information
        """
        try:
            analyst_output = json.loads(analyst_output_json)
        except json.JSONDecodeError:
            return "Error: Could not parse analyst output."

        result = _format_analyst_output(shared_instances, analyst_output)

        if result.get("success"):
            return result.get("formatted_answer", "No results.")
        else:
            return f"Error: {result.get('error', 'Formatting failed')}"

    # ── Create Router Agent ─────────────────────────────────
    agent = ChatAgent(
        name="RouterAgent",
        chat_client=chat_client,
        instructions=ROUTER_AGENT_PROMPT,
        tools=[extract_intent, run_transactions_analyst, run_feedback_analyst, format_results],
    )

    print("[OK] Router Agent created with tools: extract_intent, run_transactions_analyst, run_feedback_analyst, format_results")
    print(f"[OK] Domains registered: {', '.join(DOMAIN_REGISTRY.keys())}")

    return agent


# ============================================================
# Internal helpers
# ============================================================

def _run_analyst(workflow, shared: Dict, user_question: str, intent: str) -> str:
    """Run an analyst workflow and return JSON result string."""
    # Load schema for this domain
    schema_file = DOMAIN_REGISTRY[intent]["schema_file"]
    schema_path = project_root / schema_file

    try:
        schema_content = schema_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return json.dumps({"success": False, "error": f"Schema file not found: {schema_file}"})

    # Get access token from current workflow state (set by app.py)
    state = get_workflow_state()
    access_token = state.access_token if state else None

    result = run_analyst_workflow_sync(
        workflow=workflow,
        user_query=user_question,
        intent=intent,
        schema_content=schema_content,
        access_token=access_token,
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
