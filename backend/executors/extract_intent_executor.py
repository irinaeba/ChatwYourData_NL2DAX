# backend/executors/extract_intent_executor.py
"""
Extract Intent Executor

Workflow step 1: Analyze user query to determine intent (transactions/feedback)
and load the appropriate pre-filtered schema.
"""

import logging
import time
from pathlib import Path
from typing import Dict, Any

from agent_framework import Executor, WorkflowContext, handler

logger = logging.getLogger(__name__)


class ExtractIntentExecutor(Executor):
    """
    Executor Step 1: Extract intent and load schema.
    
    This executor:
    1. Analyzes the user query using keyword matching
    2. Determines if it's about TRANSACTIONS or FEEDBACK
    3. Loads the appropriate pre-filtered schema file
    
    Input: user_query (str)
    Output: Passes state dict to next executor
    """
    
    def __init__(self, intent_extractor=None):
        """
        Initialize the executor.
        
        Args:
            intent_extractor: IntentExtractor instance (shared for performance)
        """
        super().__init__(id="extract_intent")
        self._intent_extractor = intent_extractor
    
    @handler
    async def handle_query(self, query: str, ctx: WorkflowContext[Dict[str, Any]]) -> None:
        """
        Extract intent from user query and load schema.
        
        Args:
            query: The user's natural language question
            ctx: Workflow context for sending results downstream
        """
        step_start = time.time()
        from backend.executors.workflow_state import get_workflow_state
        
        state = get_workflow_state()
        state.current_step = "extract_intent"
        state.user_query = query
        state.original_user_query = query
        
        print(f"\n{'─'*60}")
        print(f"[STEP 1/5] EXTRACT INTENT")
        print(f"{'─'*60}")
        print(f"   [TIMING] Step started at {time.strftime('%H:%M:%S')}")
        print(f"   Query: {query[:80]}...")
        
        try:
            # Use injected extractor or create new one
            if self._intent_extractor is None:
                from backend.tools.extract_intent import IntentExtractor
                self._intent_extractor = IntentExtractor()
            
            result = self._intent_extractor.extract(query)
            
            if not result.success:
                state.error = f"Intent extraction failed: {result.error}"
                print(f"   [ERROR] {state.error}")
                await ctx.send_message({"success": False, "error": state.error, "step": "extract_intent"})
                return
            
            # Update state
            state.intent = result.intent
            state.confidence = result.confidence
            state.schema_path = result.schema_file
            state.matched_keywords = result.matched_keywords
            
            # Load schema content
            schema_file = Path(result.schema_file)
            if not schema_file.exists():
                state.error = f"Schema file not found: {result.schema_file}"
                print(f"   [ERROR] {state.error}")
                await ctx.send_message({"success": False, "error": state.error, "step": "extract_intent"})
                return
            
            state.schema_content = schema_file.read_text(encoding="utf-8")
            state.steps_completed.append("extract_intent")
            
            # Record step timing
            step_elapsed = time.time() - step_start
            state.step_timings["extract_intent"] = step_elapsed
            
            print(f"   Intent: {state.intent.upper()}")
            print(f"   Confidence: {state.confidence:.0%}")
            print(f"   Keywords: {', '.join(state.matched_keywords[:5])}")
            print(f"   Schema: {state.schema_path}")
            print(f"   [OK] Step 1 complete")
            
            # Pass to next executor
            await ctx.send_message({
                "success": True,
                "step": "extract_intent",
                "intent": state.intent,
                "confidence": state.confidence,
                "schema_path": state.schema_path,
            })
            
        except Exception as e:
            state.error = str(e)
            logger.error(f"[ExtractIntent] Error: {e}")
            print(f"   [ERROR] Exception: {e}")
            await ctx.send_message({"success": False, "error": str(e), "step": "extract_intent"})
