# backend/executors/generate_dax_executor.py
"""
Generate DAX Executor

Workflow step 2: Generate DAX query using Azure OpenAI LLM.
"""

import logging
import time
from typing import Dict, Any

from agent_framework import Executor, WorkflowContext, handler

logger = logging.getLogger(__name__)


class GenerateDAXExecutor(Executor):
    """
    Executor Step 2: Generate DAX query.
    
    This executor:
    1. Takes the schema content from state
    2. Uses Azure OpenAI to generate a DAX query
    3. Stores the generated query in state
    
    Input: Result from ExtractIntentExecutor
    Output: Passes state dict to next executor
    """
    
    def __init__(self, dax_generator=None):
        """
        Initialize the executor.
        
        Args:
            dax_generator: DAXGenerator instance (shared for performance)
        """
        super().__init__(id="generate_dax")
        self._dax_generator = dax_generator
    
    @handler
    async def handle_message(self, message: Dict[str, Any], ctx: WorkflowContext[Dict[str, Any]]) -> None:
        """
        Generate DAX from user query.
        
        Args:
            message: Result from previous executor
            ctx: Workflow context for sending results downstream
        """
        step_start = time.time()
        from backend.executors.workflow_state import get_workflow_state
        
        state = get_workflow_state()
        state.current_step = "generate_dax"
        
        print(f"\n{'─'*60}")
        print(f"[STEP 2/5] GENERATE DAX")
        print(f"{'─'*60}")
        print(f"   [TIMING] Step started at {time.strftime('%H:%M:%S')}")
        
        # Check previous step
        if not message.get("success"):
            error = message.get("error", "Previous step failed")
            print(f"   [SKIP] Previous step failed: {error}")
            await ctx.send_message(message)
            return
        
        try:
            # Use injected generator or create new one
            if self._dax_generator is None:
                from backend.tools.generate_dax import DAXGenerator
                self._dax_generator = DAXGenerator()
            
            print(f"   Generating DAX for: {state.user_query[:60]}...")
            print(f"   Using {state.intent.upper()} prompt")
            
            # Generate DAX with conversation history for follow-ups
            # Pass intent to select the correct domain-specific prompt
            llm_start = time.time()
            result = self._dax_generator.generate(
                state.user_query,
                state.schema_content,
                state.conversation_history,
                intent=state.intent
            )
            llm_elapsed = time.time() - llm_start
            print(f"   [TIMING] LLM generation took {llm_elapsed:.2f}s")
            
            if not result.success:
                state.error = f"DAX generation failed: {result.error}"
                print(f"   [ERROR] {state.error}")
                await ctx.send_message({"success": False, "error": state.error, "step": "generate_dax"})
                return
            
            # Update state
            state.generated_dax = result.query
            state.generation_notes = result.notes
            state.used_tables = result.used_tables
            state.used_columns = result.used_columns
            state.used_measures = result.used_measures
            state.steps_completed.append("generate_dax")
            
            # Capture TTFT/TTLT timing if available
            if result.timing:
                state.dax_generation_ttft = result.timing.get("ttft")
                state.dax_generation_ttlt = result.timing.get("ttlt")
                print(f"   [TIMING] TTFT: {state.dax_generation_ttft:.3f}s, TTLT: {state.dax_generation_ttlt:.3f}s")
            
            # Record step timing
            step_elapsed = time.time() - step_start
            state.step_timings["generate_dax"] = step_elapsed
            
            print(f"\n   === GENERATED DAX ===")
            print(f"   {state.generated_dax}")
            print(f"   === END DAX ===\n")
            if state.generation_notes:
                print(f"   Notes: {state.generation_notes}")
            print(f"   [TIMING] Step 2 completed in {step_elapsed:.2f}s")
            print(f"   [OK] Step 2 complete")
            
            await ctx.send_message({
                "success": True,
                "step": "generate_dax",
                "dax_query": state.generated_dax,
            })
            
        except Exception as e:
            state.error = str(e)
            logger.error(f"[GenerateDAX] Error: {e}")
            print(f"   [ERROR] Exception: {e}")
            await ctx.send_message({"success": False, "error": str(e), "step": "generate_dax"})
