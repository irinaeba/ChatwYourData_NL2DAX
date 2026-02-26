# backend/executors/validate_dax_executor.py
"""
Validate DAX Executor

Workflow step 3: Validate and optionally correct the generated DAX.
Supports retry mode when execute_dax fails - uses error context to fix DAX.
"""

import logging
import time
from typing import Dict, Any

from agent_framework import Executor, WorkflowContext, handler

logger = logging.getLogger(__name__)


class ValidateDAXExecutor(Executor):
    """
    Executor Step 3: Validate DAX query.
    
    This executor:
    1. Takes the generated DAX from state
    2. Validates syntax and semantic correctness
    3. Optionally corrects issues
    4. Stores final DAX in state
    
    RETRY MODE:
    - When state.phase == "RETRY_VALIDATE", uses execution error to fix DAX
    - Includes failed DAX and error message in validation context
    
    Input: Result from GenerateDAXExecutor (or ExecuteDAXExecutor on retry)
    Output: Passes state dict to next executor
    """
    
    def __init__(self, dax_validator=None):
        """
        Initialize the executor.
        
        Args:
            dax_validator: DAXValidator instance (shared for performance)
        """
        super().__init__(id="validate_dax")
        self._dax_validator = dax_validator
    
    @handler
    async def handle_message(self, message: Dict[str, Any], ctx: WorkflowContext[Dict[str, Any]]) -> None:
        """
        Validate the generated DAX query.
        
        Args:
            message: Result from previous executor
            ctx: Workflow context for sending results downstream
        """
        step_start = time.time()
        from backend.executors.workflow_state import get_workflow_state
        
        state = get_workflow_state()
        state.current_step = "validate_dax"
        
        # Check if this is a retry
        is_retry = state.phase == "RETRY_VALIDATE"
        retry_info = f" (RETRY {state.retry_count}/{state.max_retries})" if is_retry else ""
        
        print(f"\n{'─'*60}")
        print(f"[STEP 3b] VALIDATE DAX (retry path){retry_info}")
        print(f"{'─'*60}")
        print(f"   [TIMING] Step started at {time.strftime('%H:%M:%S')}")
        
        if is_retry:
            print(f"   Retrying due to execution error: {state.last_execution_error[:80]}...")
            # In retry mode, we always proceed (don't check message success)
            state.phase = "NORMAL"  # Reset phase
        else:
            # Check previous step only in normal mode
            if not message.get("success"):
                error = message.get("error", "Previous step failed")
                print(f"   [SKIP] Previous step failed: {error}")
                await ctx.send_message(message)
                return
        
        if not state.generated_dax:
            state.error = "No DAX query to validate"
            print(f"   [ERROR] {state.error}")
            await ctx.send_message({"success": False, "error": state.error, "step": "validate_dax"})
            return
        
        try:
            # Use injected validator or create new one
            if self._dax_validator is None:
                from backend.tools.validate_dax import DAXValidator  # Changed from dax_validator
                self._dax_validator = DAXValidator()
            
            # Build validation context
            dax_to_validate = state.final_dax if is_retry else state.generated_dax
            print(f"   Validating: {dax_to_validate[:60]}...")
            
            # If retry, add error context to the user query for better correction
            user_query_with_context = state.user_query
            if is_retry and state.last_execution_error:
                user_query_with_context = f"""{state.user_query}

IMPORTANT - PREVIOUS DAX FAILED WITH ERROR:
{state.last_execution_error}

FAILED DAX:
{state.final_dax}

Please fix the DAX to resolve this error."""
                print(f"   Including error context for retry...")
            
            print(f"   Using {state.intent.upper()} validation prompt")
            
            # Pass intent to select the correct domain-specific validation prompt
            llm_start = time.time()
            result = self._dax_validator.validate(
                user_query_with_context,
                dax_to_validate,
                state.schema_content,
                intent=state.intent
            )
            llm_elapsed = time.time() - llm_start
            print(f"   [TIMING] LLM validation took {llm_elapsed:.2f}s")
            
            # Update state
            state.is_valid = result.is_valid if result.success else False
            state.validation_issues = result.issues if result.success else []
            state.validation_suggestions = []  # Not returned by current validator
            state.corrected_dax = result.corrected_query if result.success else None  # Changed from corrected_dax to corrected_query
            
            # Store chart metadata from validation
            if result.chart_metadata:
                state.chart_metric_name = result.chart_metadata.metric_name
                state.chart_dimension = result.chart_metadata.dimension
                state.chart_dimension_type = result.chart_metadata.dimension_type
                print(f"   Chart metadata: metric={state.chart_metric_name}, dimension={state.chart_dimension}, type={state.chart_dimension_type}")
            
            # Use corrected DAX if available, otherwise use original
            state.final_dax = state.corrected_dax or dax_to_validate
            
            # Track this step (but don't duplicate on retry)
            if "validate_dax" not in state.steps_completed:
                state.steps_completed.append("validate_dax")
            
            # Record step timing
            step_elapsed = time.time() - step_start
            state.step_timings["validate_dax"] = step_elapsed
            
            if state.is_valid:
                print(f"   [OK] DAX is valid")
            else:
                print(f"   Issues: {len(state.validation_issues)}")
                for issue in state.validation_issues[:3]:
                    print(f"      - {issue}")
                if state.corrected_dax:
                    print(f"   [OK] DAX was corrected")
            
            print(f"\n   === FINAL DAX (after validation) ===")
            print(f"   {state.final_dax}")
            print(f"   === END DAX ===\n")
            
            if state.corrected_dax and state.corrected_dax != dax_to_validate:
                print(f"   Note: DAX was modified during validation")
            print(f"   [OK] Step 3 complete")
            
            await ctx.send_message({
                "success": True,
                "step": "validate_dax",
                "is_valid": state.is_valid,
                "final_dax": state.final_dax,
                "issues": state.validation_issues,
                "is_retry": is_retry,
            })
            
        except Exception as e:
            # Validation errors shouldn't stop the workflow - use original DAX
            logger.warning(f"[ValidateDAX] Warning: {e}")
            print(f"   [WARN] Validation error: {e}")
            print(f"   [OK] Continuing with original DAX")
            
            state.final_dax = state.generated_dax
            if "validate_dax" not in state.steps_completed:
                state.steps_completed.append("validate_dax")
            
            # Record step timing even on error
            step_elapsed = time.time() - step_start
            state.step_timings["validate_dax"] = step_elapsed
            
            await ctx.send_message({
                "success": True,
                "step": "validate_dax",
                "is_valid": False,
                "final_dax": state.final_dax,
                "issues": [str(e)],
            })
