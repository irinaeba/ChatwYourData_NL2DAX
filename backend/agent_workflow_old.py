"""
Agent Workflow - Shared DAX Agent Components

This module contains the workflow and agent creation logic used by:
- run_devui.py (DevUI interface)
- run_evaluation.py (Batch evaluation)

Exports:
- create_dax_workflow() - Creates the 5-step executor workflow
- run_workflow() - Runs the workflow for a single query
- create_dax_agent() - Creates the ChatAgent with workflow tool
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

# Import executors
from backend.executors import (
    ExtractIntentExecutor,
    GenerateDAXExecutor,
    ValidateDAXExecutor,
    ExecuteDAXExecutor,
    FormatResultsExecutor,
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

# Import prompts
from backend.prompts.agent_workflow_prompt import WORKFLOW_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


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


def create_dax_workflow(pre_connect_powerbi: bool = True):
    """
    Create the DAX workflow with all executors.
    
    Args:
        pre_connect_powerbi: Whether to pre-connect to Power BI (default True)
    
    Returns:
        tuple: (workflow, shared_instances dict)
    """
    print("[INIT] Creating DAX Workflow with Executors...")
    
    # ========================================
    # Pre-initialize shared instances
    # ========================================
    print("[PERF] Pre-initializing shared instances...")
    
    shared = {
        "intent_extractor": IntentExtractor(),
        "dax_generator": DAXGenerator(),
        "dax_validator": DAXValidator(),
        "results_formatter": get_formatter(),
        "dax_executor": get_executor(),  # Always create (connection deferred until token is set)
    }
    print("[PERF] LLM instances ready")
    print("[PERF] DAX Executor created (connection deferred until user authenticates)")
    
    # Pre-connect to Power BI (only when token is already available, e.g. service principal mode)
    if pre_connect_powerbi:
        print("[PERF] Pre-connecting to Power BI XMLA endpoint...")
        try:
            shared["dax_executor"]._ensure_connection()
            print("[PERF] Power BI connection established")
        except Exception as e:
            print(f"[WARN] Could not pre-connect: {e}")
            print("[WARN] Will connect on first query")
    
    # ========================================
    # Create executors with shared instances
    # ========================================
    print("[INIT] Creating executor nodes...")
    
    extract_intent = ExtractIntentExecutor(intent_extractor=shared["intent_extractor"])
    generate_dax = GenerateDAXExecutor(dax_generator=shared["dax_generator"])
    validate_dax = ValidateDAXExecutor(dax_validator=shared["dax_validator"])
    execute_dax = ExecuteDAXExecutor(dax_executor=shared["dax_executor"])
    format_results = FormatResultsExecutor(results_formatter=shared["results_formatter"])
    
    # ========================================
    # Build workflow pipeline with conditional edges
    # OPTIMISTIC EXECUTION: Try execute first, validate only on failure
    # ========================================
    print("[INIT] Building workflow pipeline (optimistic execution)...")
    
    builder = WorkflowBuilder()
    builder.set_start_executor(extract_intent)
    
    # Linear edges: Extract Intent → Generate DAX → Execute DAX (skip validation initially)
    builder.add_edge(extract_intent, generate_dax)
    builder.add_edge(generate_dax, execute_dax)  # Go directly to execute
    
    # From ValidateDAX (only runs on retry) → Execute DAX
    builder.add_edge(validate_dax, execute_dax)
    
    # Conditional edges from execute_dax based on phase
    # - On success (phase=FORMAT): go to format_results
    # - On retry needed (phase=RETRY_VALIDATE): go to validate_dax (first failure triggers validation)
    # - On failure (phase=FAILED): go to format_results to show error
    builder.add_switch_case_edge_group(
        execute_dax,
        [
            Case(
                condition=lambda message: message.get("phase") == "RETRY_VALIDATE",
                target=validate_dax,
            ),
            Default(target=format_results),  # FORMAT, FAILED, or success all go to format
        ],
    )
    
    workflow = builder.build()
    
    print("[OK] Workflow pipeline (optimistic):")
    print("     ExtractIntent -> GenerateDAX -> ExecuteDAX")
    print("                                        |")
    print("                          SUCCESS       |   FAIL")
    print("                            v           v")
    print("                      FormatResults <- ValidateDAX")
    print("                                        |")
    print("                                        v")
    print("                                   ExecuteDAX (retry)")
    
    return workflow, shared


async def run_workflow(workflow, user_query: str, access_token: str = None) -> Dict[str, Any]:
    """
    Run the workflow for a user query.
    
    Args:
        workflow: The built workflow
        user_query: User's natural language question
        access_token: User's Power BI access token from frontend MSAL.js
        
    Returns:
        Dict with formatted_answer and metadata
    """
    start_time = time.time()
    
    # Reset state for new query (preserves conversation history)
    reset_start = time.time()
    state = reset_workflow_state()
    state.user_query = user_query
    state.original_user_query = user_query
    state.access_token = access_token  # Set user's token for Power BI calls
    print(f"[TIMING] State reset in {time.time() - reset_start:.3f}s")
    
    print(f"\n{'='*70}")
    print(f"[WORKFLOW START]")
    print(f"   Query: {user_query}")
    print(f"{'='*70}")
    
    final_result = None
    event_count = 0
    last_event_time = time.time()
    
    stream_start = time.time()
    print(f"[TIMING] Starting workflow stream...")
    async for event in workflow.run_stream(user_query):
        event_count += 1
        now = time.time()
        event_elapsed = now - last_event_time
        print(f"[TIMING] Event #{event_count} received after {event_elapsed:.2f}s (type: {type(event).__name__})")
        last_event_time = now
        
        if isinstance(event, WorkflowOutputEvent):
            final_result = event.output
            print(f"[TIMING] Final output event received")
            print(f"[DEBUG] final_result keys: {final_result.keys() if final_result else 'None'}")
            print(f"[DEBUG] chart_config in result: {final_result.get('chart_config') is not None if final_result else 'N/A'}")
    
    stream_elapsed = time.time() - stream_start
    print(f"[TIMING] Workflow stream completed: {event_count} events in {stream_elapsed:.2f}s")
    
    # Calculate elapsed time
    elapsed_time = time.time() - start_time
    
    if final_result:
        final_result["elapsed_time"] = elapsed_time
        return final_result
    
    # Fallback to state
    state = get_workflow_state()
    print(f"[DEBUG] Fallback: chart_config in state: {state.chart_config is not None}")
    return {
        "success": bool(state.formatted_answer),
        "formatted_answer": state.formatted_answer or "No result generated",
        "steps_completed": state.steps_completed,
        "error": state.error,
        "elapsed_time": elapsed_time,
        "chart_config": state.chart_config,
        "chart_type": state.chart_type,
        "dax_query": state.final_dax,
        "row_count": state.row_count,
        "requires_reauth": state.requires_reauth,
    }


def run_workflow_sync(workflow, user_query: str, timeout: int = 120, access_token: str = None) -> Dict[str, Any]:
    """
    Run the workflow synchronously (blocking).
    
    This wraps run_workflow in a thread to avoid event loop conflicts.
    
    Args:
        workflow: The built workflow
        user_query: User's natural language question
        timeout: Timeout in seconds (default 120)
        access_token: User's Power BI access token from frontend MSAL.js
        
    Returns:
        Dict with formatted_answer and metadata
    """
    sync_start = time.time()
    print(f"[TIMING] run_workflow_sync started")
    
    def run_async_workflow():
        """Run workflow in a new event loop in a separate thread."""
        thread_start = time.time()
        print(f"[TIMING] Thread started, creating event loop...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_created = time.time()
        print(f"[TIMING] Event loop created in {loop_created - thread_start:.3f}s")
        try:
            result = loop.run_until_complete(run_workflow(workflow, user_query, access_token=access_token))
            print(f"[TIMING] Async workflow completed in thread: {time.time() - thread_start:.2f}s")
            return result
        finally:
            loop.close()
    
    try:
        # Run in a thread pool to avoid event loop conflicts
        executor_start = time.time()
        print(f"[TIMING] Creating ThreadPoolExecutor...")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            print(f"[TIMING] Executor created in {time.time() - executor_start:.3f}s, submitting task...")
            submit_start = time.time()
            future = executor.submit(run_async_workflow)
            print(f"[TIMING] Task submitted in {time.time() - submit_start:.3f}s, waiting for result...")
            wait_start = time.time()
            result = future.result(timeout=timeout)
            print(f"[TIMING] Result received after {time.time() - wait_start:.2f}s wait")
        print(f"[TIMING] run_workflow_sync total: {time.time() - sync_start:.2f}s")
        return result
    except concurrent.futures.TimeoutError:
        return {
            "success": False,
            "error": f"Workflow timed out after {timeout} seconds",
            "formatted_answer": None,
            "steps_completed": [],
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "formatted_answer": None,
            "steps_completed": [],
        }


def create_dax_agent(workflow=None, shared_instances=None):
    """
    Create the DAX agent with workflow tool.
    
    Args:
        workflow: Pre-built workflow (creates new if None)
        shared_instances: Shared tool instances (creates new if None)
        
    Returns:
        ChatAgent configured with the DAX workflow tool
    """
    print("[INIT] Initializing DAX Agent (Workflow Mode)...")
    
    config = DAXAgentConfig()
    config.validate()
    
    # Create credential
    credential = ClientSecretCredential(
        tenant_id=config.tenant_id,
        client_id=config.client_id,
        client_secret=config.client_secret,
    )
    
    # Create chat client for the agent
    chat_client = AzureOpenAIChatClient(
        endpoint=config.endpoint,
        credential=credential,
        deployment_name=config.deployment_name,
        api_version=config.api_version,
    )
    
    # Create workflow if not provided
    if workflow is None:
        workflow, shared_instances = create_dax_workflow()
    
    # ========================================
    # Workflow Tool for Agent
    # ========================================
    def run_dax_workflow(user_question: str) -> str:
        """
        Execute the complete DAX workflow for a user question.
        
        This tool runs a 5-step workflow:
        1. Extract intent and load appropriate schema
        2. Generate DAX query using LLM
        3. Validate and improve the DAX
        4. Execute against Power BI
        5. Format results into human-readable text
        
        Each step is an Executor node with full visibility and retry support.
        
        Args:
            user_question: The user's natural language question about data
            
        Returns:
            Formatted answer with results, explanation, and DAX query
        """
        tool_start_time = time.time()
        print(f"\n[TIMING] Tool called at {time.strftime('%H:%M:%S')}")
        
        try:
            workflow_call_start = time.time()
            result = run_workflow_sync(workflow, user_question, timeout=120)
            workflow_call_elapsed = time.time() - workflow_call_start
            print(f"[TIMING] run_workflow_sync completed in {workflow_call_elapsed:.2f}s")
            
            # Calculate total elapsed time
            total_elapsed = time.time() - tool_start_time
            print(f"[TIMING] Total tool execution: {total_elapsed:.2f}s")
            
            # Return formatted answer or error
            if result.get("success") and result.get("formatted_answer"):
                answer = result["formatted_answer"]
                # Append timing info to the answer
                answer += f"\n\n---\n*Total execution time: {total_elapsed:.2f} seconds*"
                return answer
            else:
                error = result.get("error", "Unknown error")
                steps = result.get("steps_completed", [])
                return f"""### Error

The workflow encountered an error: **{error}**

### Steps Completed

{', '.join(steps) if steps else 'None'}

Please try rephrasing your question or check the connection to Power BI.
"""
        except Exception as e:
            error_details = traceback.format_exc()
            print(f"[ERROR] Workflow exception: {e}")
            print(f"[ERROR] Details: {error_details}")
            return f"""### Error

An unexpected error occurred: **{str(e)}**

Please check the console logs for more details.
"""
    
    # ========================================
    # Create Agent with Workflow Tool
    # ========================================
    agent = ChatAgent(
        name="DAXAgent",
        chat_client=chat_client,
        instructions=WORKFLOW_SYSTEM_PROMPT,
        tools=[run_dax_workflow]
    )
    
    print("[OK] Agent created with workflow tool")
    
    return agent
