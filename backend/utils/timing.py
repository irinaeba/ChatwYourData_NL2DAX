"""
Timing utilities for tracking execution performance.
"""

import time
from typing import Dict, Optional
from dataclasses import dataclass, field


@dataclass
class ExecutionTiming:
    """Tracks timing information for query execution."""
    
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    executor_times: Dict[str, float] = field(default_factory=dict)
    _executor_starts: Dict[str, float] = field(default_factory=dict)
    
    def start_executor(self, executor_name: str) -> None:
        """Mark the start of an executor's execution."""
        self._executor_starts[executor_name] = time.time()
    
    def end_executor(self, executor_name: str) -> float:
        """Mark the end of an executor's execution and return elapsed time."""
        if executor_name not in self._executor_starts:
            return 0.0
        elapsed = time.time() - self._executor_starts[executor_name]
        self.executor_times[executor_name] = round(elapsed, 3)
        del self._executor_starts[executor_name]
        return elapsed
    
    def finish(self) -> None:
        """Mark the end of total execution."""
        self.end_time = time.time()
    
    @property
    def total_time(self) -> float:
        """Get total execution time in seconds."""
        end = self.end_time or time.time()
        return round(end - self.start_time, 3)
    
    def to_dict(self) -> Dict[str, any]:
        """Convert timing info to dictionary for API response."""
        return {
            "total_seconds": self.total_time,
            "executors": self.executor_times.copy()
        }
