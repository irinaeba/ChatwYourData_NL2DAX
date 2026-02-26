# backend/executors/execute_dax_executor.py
"""
Execute DAX Executor

Workflow step 4: Execute the DAX query against Power BI XMLA endpoint.

RETRY MECHANISM:
- On failure, sets state.phase = "RETRY_VALIDATE" to trigger re-validation
- Tracks retry_count to prevent infinite loops
- After max_retries, sets state.phase = "FAILED"
"""

import logging
import time
from typing import Dict, Any

from agent_framework import Executor, WorkflowContext, handler

logger = logging.getLogger(__name__)


class ExecuteDAXExecutor(Executor):
    """
    Executor Step 4: Execute DAX query.
    
    This executor:
    1. Takes the final DAX from state
    2. Executes against Power BI using XMLA endpoint
    3. On success: stores results, sets phase = "FORMAT"
    4. On failure: sets phase = "RETRY_VALIDATE" (if retries available)
    
    Input: Result from ValidateDAXExecutor
    Output: Passes state dict to next executor (or back to validate on retry)
    """
    
    def __init__(self, dax_executor=None):
        """
        Initialize the executor.
        
        Args:
            dax_executor: DAXExecutor instance (shared connection)
        """
        super().__init__(id="execute_dax")
        self._dax_executor = dax_executor
    
    @handler
    async def handle_message(self, message: Dict[str, Any], ctx: WorkflowContext[Dict[str, Any]]) -> None:
        """
        Execute the DAX query against Power BI.
        
        Args:
            message: Result from previous executor
            ctx: Workflow context for sending results downstream
        """
        step_start = time.time()
        from backend.executors.workflow_state import get_workflow_state, ConversationTurn
        
        state = get_workflow_state()
        state.current_step = "execute_dax"
        
        retry_info = f" (attempt {state.retry_count + 1}/{state.max_retries + 1})" if state.retry_count > 0 else ""
        
        print(f"\n{'─'*60}")
        print(f"[STEP 3] EXECUTE DAX{retry_info}")
        print(f"{'─'*60}")
        print(f"   [TIMING] Step started at {time.strftime('%H:%M:%S')}")
        
        # Check previous step
        if not message.get("success"):
            error = message.get("error", "Previous step failed")
            print(f"   [SKIP] Previous step failed: {error}")
            state.phase = "FAILED"
            await ctx.send_message(message)
            return
        
        # OPTIMISTIC PATH: If final_dax not set (validation skipped), use generated_dax
        if not state.final_dax and state.generated_dax:
            state.final_dax = state.generated_dax
            print(f"   [INFO] Using generated DAX directly (validation skipped)")
        
        if not state.final_dax:
            state.error = "No DAX query to execute"
            print(f"   [ERROR] {state.error}")
            state.phase = "FAILED"
            await ctx.send_message({"success": False, "error": state.error, "step": "execute_dax", "phase": "FAILED"})
            return
        
        try:
            # Use injected executor or get global one
            if self._dax_executor is None:
                from backend.tools.execute_dax import get_executor
                self._dax_executor = get_executor()
            
            # Set user's access token if available (from frontend MSAL.js)
            if state.access_token:
                # Check if token matches what's already set (avoids unnecessary disconnect)
                current_token = getattr(self._dax_executor, '_user_access_token', None)
                if current_token and current_token == state.access_token:
                    print(f"   [AUTH] Token already set and matches (executor id={id(self._dax_executor)}, connected={self._dax_executor._connected})")
                else:
                    print(f"   [AUTH] Setting access token on executor (id={id(self._dax_executor)})")
                    self._dax_executor.set_access_token(state.access_token)
            
            print(f"   Executing: {state.final_dax[:60]}...")
            
            exec_start = time.time()
            result = self._dax_executor.execute(state.final_dax)
            exec_elapsed = time.time() - exec_start
            print(f"   [TIMING] Power BI execution took {exec_elapsed:.2f}s")
            
            if not result.success:
                # Execution failed - check if we can retry
                error_msg = result.error or "Unknown execution error"
                state.last_execution_error = error_msg
                state.failed_dax_queries.append(state.final_dax)
                
                # Track if this is the initial (first) execution failure
                if state.retry_count == 0:
                    state.initial_execution_failed = True
                
                # Check if this is an authentication error (no retry possible)
                if getattr(result, 'requires_reauth', False):
                    state.phase = "FAILED"
                    state.error = error_msg
                    state.requires_reauth = True
                    print(f"   [AUTH ERROR] {error_msg}")
                    print(f"   [FAILED] Authentication required - user must re-authenticate")
                    
                    await ctx.send_message({
                        "success": False, 
                        "error": error_msg, 
                        "step": "execute_dax",
                        "phase": "FAILED",
                        "requires_reauth": True,
                    })
                    return
                
                if state.retry_count < state.max_retries:
                    # Trigger retry
                    state.retry_count += 1
                    state.phase = "RETRY_VALIDATE"
                    print(f"   [ERROR] {error_msg}")
                    print(f"   [RETRY] Will retry ({state.retry_count}/{state.max_retries}) - returning to validation")
                    
                    await ctx.send_message({
                        "success": False, 
                        "error": error_msg, 
                        "step": "execute_dax",
                        "phase": "RETRY_VALIDATE",
                        "retry_count": state.retry_count,
                    })
                else:
                    # Max retries reached
                    state.phase = "FAILED"
                    state.error = f"DAX execution failed after {state.max_retries} retries: {error_msg}"
                    print(f"   [ERROR] {error_msg}")
                    print(f"   [FAILED] Max retries ({state.max_retries}) reached - giving up")
                    
                    await ctx.send_message({
                        "success": False, 
                        "error": state.error, 
                        "step": "execute_dax",
                        "phase": "FAILED",
                    })
                return
            
            # Success!
            state.execution_success = True
            state.columns = result.columns
            state.data = result.data
            state.row_count = result.row_count
            state.phase = "FORMAT"  # Signal to go to format_results
            
            # Track step (don't duplicate)
            if "execute_dax" not in state.steps_completed:
                state.steps_completed.append("execute_dax")
            
            # Record step timing
            step_elapsed = time.time() - step_start
            state.step_timings["execute_dax"] = step_elapsed
            
            # Add to conversation history for follow-up questions
            state.conversation_history.append(ConversationTurn(
                user_query=state.user_query,
                dax_query=state.final_dax,
                intent=state.intent
            ))
            # Keep only last 5 turns
            if len(state.conversation_history) > 5:
                state.conversation_history = state.conversation_history[-5:]
            
            print(f"   Rows: {state.row_count}")
            print(f"   Columns: {', '.join(state.columns[:5])}{'...' if len(state.columns) > 5 else ''}")
            if state.retry_count > 0:
                print(f"   [OK] Succeeded after {state.retry_count} retry(ies)")
            print(f"   [OK] Step 4 complete")
            
            await ctx.send_message({
                "success": True,
                "step": "execute_dax",
                "phase": "FORMAT",
                "row_count": state.row_count,
                "columns": state.columns,
            })
            
        except Exception as e:
            error_msg = str(e)
            state.last_execution_error = error_msg
            state.failed_dax_queries.append(state.final_dax)
            
            if state.retry_count < state.max_retries:
                # Trigger retry
                state.retry_count += 1
                state.phase = "RETRY_VALIDATE"
                logger.warning(f"[ExecuteDAX] Error (will retry): {e}")
                print(f"   [ERROR] Exception: {e}")
                print(f"   [RETRY] Will retry ({state.retry_count}/{state.max_retries}) - returning to validation")
                
                await ctx.send_message({
                    "success": False, 
                    "error": error_msg, 
                    "step": "execute_dax",
                    "phase": "RETRY_VALIDATE",
                    "retry_count": state.retry_count,
                })
            else:
                # Max retries reached
                state.phase = "FAILED"
                state.error = f"DAX execution failed after {state.max_retries} retries: {error_msg}"
                logger.error(f"[ExecuteDAX] Error (max retries): {e}")
                print(f"   [ERROR] Exception: {e}")
                print(f"   [FAILED] Max retries ({state.max_retries}) reached - giving up")
                
                await ctx.send_message({
                    "success": False, 
                    "error": state.error, 
                    "step": "execute_dax",
                    "phase": "FAILED",
                })
