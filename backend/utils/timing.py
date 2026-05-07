"""
Timing utilities for tracking execution performance.

PipelineTiming captures the full LLM Planner → Analyst → Format pipeline:
  - LLM Planner call
  - Per-plan-step analyst execution (with executor sub-timings)
  - Result formatting
  - DAX generation streaming metrics (TTFT / TTLT)
"""

import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class PipelineTiming:
    """
    Tracks timing for the full pipeline:
        LLM Planner → Analyst workflow(s) → Format + Chart.

    Usage:
        timing = PipelineTiming()                      # starts the clock
        timing.record_planner(plan)                    # after planner returns
        timing.start_step(1, "transactions")           # before each analyst call
        timing.end_step(1, executor_timings={...})     # after each analyst call
        timing.start_format() / timing.end_format()    # around formatting
        timing.finish()                                # pipeline done
    """

    # ---- internal clocks ---------------------------------------------------
    _pipeline_start: float = field(default_factory=time.time)
    _pipeline_end: Optional[float] = None
    _step_starts: Dict[int, float] = field(default_factory=dict, repr=False)
    _format_start: Optional[float] = field(default=None, repr=False)

    # ---- LLM Planner ------------------------------------------------------
    planner_elapsed: float = 0.0
    planner_steps_count: int = 0
    is_cross_domain: bool = False
    has_dependencies: bool = False

    # ---- Per plan-step execution -------------------------------------------
    step_elapsed: Dict[int, float] = field(default_factory=dict)
    step_domains: Dict[int, str] = field(default_factory=dict)
    step_executor_timings: Dict[int, Dict[str, float]] = field(default_factory=dict)

    # ---- Native function tracking ------------------------------------------
    step_native_function: Dict[int, Optional[str]] = field(default_factory=dict)
    step_native_match_time: Dict[int, Optional[float]] = field(default_factory=dict)
    step_native_params: Dict[int, Optional[Dict[str, Any]]] = field(default_factory=dict)
    step_native_attempted: Dict[int, bool] = field(default_factory=dict)
    step_native_matched: Dict[int, bool] = field(default_factory=dict)

    # ---- Format / Chart ----------------------------------------------------
    format_elapsed: float = 0.0

    # ---- DAX generation streaming metrics ----------------------------------
    dax_generation_ttft: Optional[float] = None
    dax_generation_ttlt: Optional[float] = None

    # ---- Planner -----------------------------------------------------------
    def record_planner(self, plan) -> None:
        """Record planner metrics from an ExecutionPlan."""
        self.planner_elapsed = getattr(plan, "planner_elapsed", 0.0)
        self.planner_steps_count = len(getattr(plan, "steps", []))
        self.is_cross_domain = getattr(plan, "is_cross_domain", False)
        self.has_dependencies = getattr(plan, "has_dependencies", False)

    # ---- Analyst steps -----------------------------------------------------
    def start_step(self, step_id: int, domain: str) -> None:
        """Mark the start of a plan-step analyst call."""
        self._step_starts[step_id] = time.time()
        self.step_domains[step_id] = domain

    def record_native_attempt(
        self,
        step_id: int,
        matched: bool,
        function_name: Optional[str] = None,
        match_elapsed: float = 0.0,
    ) -> None:
        """Record that a native function match was attempted for this step."""
        self.step_native_attempted[step_id] = True
        self.step_native_matched[step_id] = matched
        self.step_native_match_time[step_id] = match_elapsed
        if function_name:
            self.step_native_function[step_id] = function_name

    def end_step(
        self,
        step_id: int,
        executor_timings: Optional[Dict[str, float]] = None,
        native_function: Optional[str] = None,
        native_match_time: Optional[float] = None,
        native_params: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Mark the end of a plan-step analyst call. Returns elapsed seconds."""
        start = self._step_starts.pop(step_id, None)
        if start is None:
            return 0.0
        elapsed = time.time() - start
        self.step_elapsed[step_id] = elapsed
        if executor_timings:
            self.step_executor_timings[step_id] = executor_timings
        if native_function:
            self.step_native_function[step_id] = native_function
            self.step_native_match_time[step_id] = native_match_time
            self.step_native_params[step_id] = native_params
        return elapsed

    # ---- Formatting --------------------------------------------------------
    def start_format(self) -> None:
        self._format_start = time.time()

    def end_format(self) -> float:
        if self._format_start is None:
            return 0.0
        self.format_elapsed = time.time() - self._format_start
        self._format_start = None
        return self.format_elapsed

    # ---- Finish ------------------------------------------------------------
    def finish(self) -> None:
        """Mark the pipeline as complete."""
        self._pipeline_end = time.time()

    @property
    def total_elapsed(self) -> float:
        end = self._pipeline_end or time.time()
        return end - self._pipeline_start

    # ---- Serialisation -----------------------------------------------------
    def to_markdown(self) -> str:
        """Build a human-readable markdown timing block for the chat answer."""
        # Collect unique domains
        domains_used = list(dict.fromkeys(
            d for d in self.step_domains.values() if d
        ))
        domains_str = ", ".join(domains_used) if domains_used else "—"

        lines = ["\n\n---\n**Execution Details:**"]
        lines.append(f"- Domains: {domains_str}")
        lines.append(
            f"- LLM Planner: {self.planner_elapsed:.2f}s "
            f"({self.planner_steps_count} step(s))"
        )

        for step_id in sorted(self.step_elapsed):
            domain = self.step_domains.get(step_id, "?")
            elapsed = self.step_elapsed[step_id]
            prefix = f"[{domain}] " if self.is_cross_domain else ""
            native_fn = self.step_native_function.get(step_id)
            native_attempted = self.step_native_attempted.get(step_id, False)
            native_matched = self.step_native_matched.get(step_id, False)
            match_time = self.step_native_match_time.get(step_id, 0.0)
            executors = self.step_executor_timings.get(step_id, {})

            if native_matched and native_fn:
                # Native function was used
                lines.append(f"- {prefix}Native function ({native_fn}): {elapsed:.2f}s")
                lines.append(f"  - Matcher: {match_time:.2f}s")
                exec_time = elapsed - (match_time or 0.0)
                lines.append(f"  - DAX execution: {max(exec_time, 0):.2f}s")
            elif native_attempted and not native_matched:
                # Native match was tried but didn't match — fell through to LLM
                lines.append(f"- {prefix}Analyst workflow: {elapsed:.2f}s")
                lines.append(f"  - Native matcher (no match): {match_time:.2f}s")
                for exec_name, exec_time in executors.items():
                    lines.append(f"  - {exec_name}: {exec_time:.2f}s")
            elif executors:
                lines.append(f"- {prefix}Analyst workflow: {elapsed:.2f}s")
                for exec_name, exec_time in executors.items():
                    lines.append(f"  - {exec_name}: {exec_time:.2f}s")
            else:
                lines.append(f"- {prefix}Analyst workflow: {elapsed:.2f}s")

        if self.format_elapsed > 0:
            lines.append(f"- Result formatting: {self.format_elapsed:.2f}s")

        lines.append(f"- **Total**: {self.total_elapsed:.2f}s")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Structured timing dict for the API response."""
        steps: List[Dict[str, Any]] = []
        for step_id in sorted(self.step_elapsed):
            step_entry: Dict[str, Any] = {
                "step_id": step_id,
                "domain": self.step_domains.get(step_id),
                "elapsed_seconds": round(self.step_elapsed[step_id], 3),
                "executors": {
                    k: round(v, 3)
                    for k, v in self.step_executor_timings.get(step_id, {}).items()
                },
            }
            native_attempted = self.step_native_attempted.get(step_id, False)
            native_matched = self.step_native_matched.get(step_id, False)
            if native_attempted:
                step_entry["native_attempted"] = True
                step_entry["native_matched"] = native_matched
                match_time = self.step_native_match_time.get(step_id)
                step_entry["native_match_seconds"] = round(match_time, 3) if match_time else None
            native_fn = self.step_native_function.get(step_id)
            if native_fn:
                step_entry["native_function"] = native_fn
                step_entry["native_params"] = self.step_native_params.get(step_id)
            steps.append(step_entry)
        return {
            "total_seconds": round(self.total_elapsed, 3),
            "planner_seconds": round(self.planner_elapsed, 3),
            "planner_steps": self.planner_steps_count,
            "is_cross_domain": self.is_cross_domain,
            "has_dependencies": self.has_dependencies,
            "format_seconds": round(self.format_elapsed, 3),
            "steps": steps,
            "dax_generation_ttft": self.dax_generation_ttft,
            "dax_generation_ttlt": self.dax_generation_ttlt,
        }
