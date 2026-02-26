# backend/executors/format_results_executor.py
"""
Format Results Executor

Workflow step 5: Format execution results into human-readable text
and create appropriate chart visualizations.
"""

import logging
import time
from typing import Dict, Any

from agent_framework import Executor, WorkflowContext, handler

logger = logging.getLogger(__name__)


class FormatResultsExecutor(Executor):
    """
    Executor Step 5: Format DAX results and create chart visualization.
    
    This executor:
    1. Takes execution results from state
    2. Uses LLM to format into human-readable markdown
    3. Creates appropriate Chart.js visualization (if applicable)
    4. Stores both formatted answer and chart config in state
    
    Input: Result from ExecuteDAXExecutor
    Output: Final workflow output with formatted_answer and chart_config
    """
    
    def __init__(self, results_formatter=None, chart_visualizer=None):
        """
        Initialize the executor.
        
        Args:
            results_formatter: DAXResultsFormatter instance (shared for performance)
            chart_visualizer: ChartVisualizer instance (shared for performance)
        """
        super().__init__(id="format_results")
        self._results_formatter = results_formatter
        self._chart_visualizer = chart_visualizer
    
    @handler
    async def handle_message(self, message: Dict[str, Any], ctx: WorkflowContext[Dict[str, Any]]) -> None:
        """
        Format the execution results.
        
        Args:
            message: Result from previous executor
            ctx: Workflow context for sending final output
        """
        step_start = time.time()
        from backend.executors.workflow_state import get_workflow_state
        
        state = get_workflow_state()
        state.current_step = "format_results"
        
        print(f"\n{'─'*60}")
        print(f"[STEP 4] FORMAT RESULTS")
        print(f"{'─'*60}")
        print(f"   [TIMING] Step started at {time.strftime('%H:%M:%S')}")
        
        # Check previous step
        if not message.get("success"):
            error = message.get("error", "Previous step failed")
            print(f"   [SKIP] Previous step failed: {error}")
            
            # Create error response
            state.formatted_answer = self._create_error_response(state, error)
            await ctx.send_message({
                "success": False,
                "error": error,
                "step": "format_results",
                "formatted_answer": state.formatted_answer,
                "chart_config": None,
                "chart_type": "none",
                "steps_completed": state.steps_completed,
                "dax_query": state.final_dax or state.generated_dax,
            })
            return
        
        try:
            # Use injected formatter or get global one
            if self._results_formatter is None:
                from backend.tools.format_dax_results import get_formatter
                self._results_formatter = get_formatter()
            
            # Use injected chart visualizer or get global one
            if self._chart_visualizer is None:
                from backend.tools.chart_visualizer import get_visualizer
                self._chart_visualizer = get_visualizer()
            
            print(f"   Formatting {state.row_count} rows...")
            print(f"   [DEBUG] DAX query to format: {state.final_dax[:100] if state.final_dax else 'None'}...")
            
            # Step 1: Format results with LLM
            format_start = time.time()
            result = self._results_formatter.format(
                user_query=state.user_query,
                dax_query=state.final_dax,
                results={
                    "columns": state.columns,
                    "data": state.data,
                    "row_count": state.row_count,
                }
            )
            
            print(f"   [DEBUG] Formatter result success: {result.success}")
            format_elapsed = time.time() - format_start
            print(f"   [TIMING] Formatter took {format_elapsed:.2f}s")
            if not result.success:
                print(f"   [DEBUG] Formatter error: {result.error}")
            
            if result.success:
                state.formatted_answer = result.formatted
                print(f"   [OK] Results formatted ({len(state.formatted_answer)} chars)")
                print(f"   [DEBUG] Formatted answer preview: {state.formatted_answer[:200]}...")
            else:
                # Fallback to basic formatting
                print(f"   [WARN] LLM formatting failed: {result.error}")
                state.formatted_answer = self._create_basic_format(state)
                print(f"   [OK] Using basic formatting")
            
            # Step 2: Create chart visualization using metadata from validation
            chart_start = time.time()
            
            # Import ChartMetadata and extraction helper
            from backend.tools.chart_visualizer import ChartMetadata, extract_chart_metadata_from_dax
            
            # Build chart metadata from state (set by validate_dax executor)
            # If validation was skipped (optimistic path), extract from DAX directly
            chart_metadata = None
            if state.chart_dimension_type and state.chart_dimension_type != "none":
                # Use metadata from validation
                chart_metadata = ChartMetadata(
                    metric_name=state.chart_metric_name,
                    dimension=state.chart_dimension,
                    dimension_type=state.chart_dimension_type,
                )
                print(f"   [DEBUG] Using chart metadata from validation: metric={state.chart_metric_name}, dim={state.chart_dimension}, type={state.chart_dimension_type}")
            elif state.final_dax and len(state.data) > 1:
                # Validation was skipped - extract chart metadata from DAX heuristically
                print(f"   [INFO] Extracting chart metadata from DAX (validation was skipped)")
                chart_metadata = extract_chart_metadata_from_dax(
                    dax_query=state.final_dax,
                    columns=state.columns,
                    user_query=state.user_query,
                )
                if chart_metadata.dimension_type != "none":
                    print(f"   [DEBUG] Extracted chart metadata: metric={chart_metadata.metric_name}, dim={chart_metadata.dimension}, type={chart_metadata.dimension_type}")
                    # Store in state for consistency
                    state.chart_metric_name = chart_metadata.metric_name
                    state.chart_dimension = chart_metadata.dimension
                    state.chart_dimension_type = chart_metadata.dimension_type
                else:
                    print(f"   [DEBUG] No chart dimension detected (single value result)")
                    chart_metadata = None
            
            chart_result = self._chart_visualizer.create_visualization(
                columns=state.columns,
                data=state.data,
                user_query=state.user_query,
                formatted_response=state.formatted_answer,
                chart_metadata=chart_metadata,
            )
            chart_elapsed = time.time() - chart_start
            print(f"   [TIMING] Chart visualization took {chart_elapsed:.3f}s")
            
            # Store chart config in state and output
            chart_config = None
            chart_type = "none"
            if chart_result.success and chart_result.chart_config:
                chart_config = chart_result.chart_config.to_dict()
                chart_type = chart_result.chart_type
                print(f"   [OK] Chart created: {chart_result.chart_type} - {chart_result.chart_config.title}")
                print(f"   [DEBUG] Chart config keys: {chart_config.keys()}")
                
                # Store in state for fallback
                state.chart_config = chart_config
                state.chart_type = chart_type
            elif chart_result.skip_reason:
                print(f"   [INFO] Chart skipped: {chart_result.skip_reason}")
            else:
                print(f"   [WARN] Chart creation failed: {chart_result.error}")
            
            # Record step timing
            step_elapsed = time.time() - step_start
            if not hasattr(state, 'step_timings') or state.step_timings is None:
                state.step_timings = {}
            state.step_timings["format_results"] = step_elapsed
            
            state.steps_completed.append("format_results")
            
            # Calculate total time from all step timings
            total_elapsed = sum(state.step_timings.values()) if state.step_timings else step_elapsed
            
            # Append timing to formatted answer
            timing_text = "\n\n---\n**⏱️ Execution Timing:**\n"
            if state.step_timings:
                for step_name, step_time in state.step_timings.items():
                    timing_text += f"- {step_name}: {step_time:.2f}s\n"
                    # Add TTFT/Gen/TTLT breakdown for generate_dax step
                    if step_name == "generate_dax" and state.dax_generation_ttft and state.dax_generation_ttlt:
                        gen_time = state.dax_generation_ttlt - state.dax_generation_ttft
                        timing_text += f"  - TTFT: {state.dax_generation_ttft:.2f}s | Gen: {gen_time:.2f}s | TTLT: {state.dax_generation_ttlt:.2f}s\n"
            timing_text += f"- **Total**: {total_elapsed:.2f}s"
            state.formatted_answer += timing_text

            print(f"\n{'='*60}")
            print(f"[WORKFLOW COMPLETE]")
            print(f"   Steps: {' -> '.join(state.steps_completed)}")
            print(f"{'='*60}\n")
            
            # Build final output
            final_output = {
                "success": True,
                "step": "format_results",
                "formatted_answer": state.formatted_answer,
                "chart_config": chart_config,
                "chart_type": chart_type,
                "steps_completed": state.steps_completed,
                "row_count": state.row_count,
                "dax_query": state.final_dax,
                "dax_generation_ttft": state.dax_generation_ttft,
                "dax_generation_ttlt": state.dax_generation_ttlt,
            }
            
            # Add timing summary
            if hasattr(state, 'step_timings') and state.step_timings:
                timing_summary = "\n\n---\n**Execution Timing:**\n"
                total_time = 0
                for step_name, step_time in state.step_timings.items():
                    timing_summary += f"- {step_name}: {step_time:.2f}s\n"
                    total_time += step_time
                timing_summary += f"- **Total**: {total_time:.2f}s"
                final_output["timing_summary"] = timing_summary
            
            # Emit output for WorkflowOutputEvent (if supported)
            try:
                await ctx.emit_output(final_output)
                print(f"   [DEBUG] emit_output called successfully")
            except AttributeError:
                print(f"   [DEBUG] emit_output not available, using send_message")
                await ctx.send_message(final_output)
            
        except Exception as e:
            logger.error(f"[FormatResults] Error: {e}")
            print(f"   [ERROR] Exception: {e}")
            
            # Fallback to basic formatting
            state.formatted_answer = self._create_basic_format(state)
            state.steps_completed.append("format_results")
            
            await ctx.send_message({
                "success": True,
                "step": "format_results",
                "formatted_answer": state.formatted_answer,
                "chart_config": None,
                "chart_type": "none",
                "steps_completed": state.steps_completed,
                "dax_query": state.final_dax,
            })
    
    def _create_basic_format(self, state) -> str:
        """Create basic formatted output if LLM formatting fails."""
        cols = state.columns or []
        data = state.data or []
        row_count = state.row_count or 0
        
        # Build markdown table
        table_lines = []
        if cols:
            table_lines.append("| " + " | ".join(str(c) for c in cols) + " |")
            table_lines.append("|" + "|".join("---" for _ in cols) + "|")
            for row in data[:15]:
                table_lines.append("| " + " | ".join(str(v) for v in row) + " |")
            if row_count > 15:
                table_lines.append(f"\n...and {row_count - 15} more rows")
        
        return f"""### Answer

Based on your question: "{state.user_query}"

### Results

{chr(10).join(table_lines) if table_lines else "No results returned."}

### Explanation

- Query returned {row_count} row(s).
"""
    
    def _create_error_response(self, state, error: str) -> str:
        """Create error response with partial results."""
        steps = state.steps_completed or []
        dax = state.final_dax or state.generated_dax
        
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
