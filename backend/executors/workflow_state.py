# backend/executors/workflow_state.py
"""
Shared workflow state for the DAX workflow.

This module provides a global state object that is passed through all
executor steps in the workflow.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from pathlib import Path


@dataclass
class ConversationTurn:
    """A single turn in the conversation history."""
    user_query: str
    dax_query: Optional[str] = None
    intent: Optional[str] = None
    result_summary: Optional[str] = None
    
    def to_context_string(self) -> str:
        """Convert to context string for LLM prompt."""
        parts = [f"User: {self.user_query}"]
        if self.dax_query:
            parts.append(f"DAX: {self.dax_query}")
        if self.result_summary:
            parts.append(f"Result: {self.result_summary}")
        if self.intent:
            parts.append(f"Intent: {self.intent}")
        return "\n".join(parts)


@dataclass
class DAXWorkflowState:
    """Shared state passed through the DAX workflow."""
    
    # User query
    user_query: Optional[str] = None
    original_user_query: Optional[str] = None
    
    # User authentication - access token from frontend MSAL.js
    access_token: Optional[str] = None
    
    # Step 1: Intent extraction
    intent: Optional[str] = None
    confidence: float = 0.0
    schema_path: Optional[str] = None
    schema_content: Optional[str] = None
    matched_keywords: List[str] = field(default_factory=list)
    
    # Step 2: DAX generation
    generated_dax: Optional[str] = None
    generation_notes: Optional[str] = None
    used_tables: List[str] = field(default_factory=list)
    used_columns: List[str] = field(default_factory=list)
    used_measures: List[str] = field(default_factory=list)
    
    # Step 3: DAX validation
    is_valid: bool = False
    validation_issues: List[str] = field(default_factory=list)
    validation_suggestions: List[str] = field(default_factory=list)
    corrected_dax: Optional[str] = None
    final_dax: Optional[str] = None
    
    # Chart metadata from validation (for chart generation)
    chart_metric_name: Optional[str] = None
    chart_dimension: Optional[str] = None
    chart_dimension_type: str = "none"  # 'date', 'categorical', or 'none'
    
    # Step 4: DAX execution
    execution_success: bool = False
    columns: List[str] = field(default_factory=list)
    data: List[List[Any]] = field(default_factory=list)
    row_count: int = 0
    execution_error: Optional[str] = None
    
    # Step 5: Formatted result
    formatted_answer: Optional[str] = None
    chart_config: Optional[Dict[str, Any]] = None
    chart_type: str = "none"
    
    # Conversation history for follow-ups
    conversation_history: List[ConversationTurn] = field(default_factory=list)
    
    # Error tracking
    current_step: Optional[str] = None
    steps_completed: List[str] = field(default_factory=list)
    error: Optional[str] = None
    requires_reauth: bool = False  # True if authentication token expired
    
    # Timing tracking
    step_timings: Dict[str, float] = field(default_factory=dict)
    
    # LLM timing breakdown for DAX generation
    dax_generation_ttft: Optional[float] = None  # Time to First Token (seconds)
    dax_generation_ttlt: Optional[float] = None  # Time to Last Token (seconds)
    
    # Retry mechanism between execute_dax and validate_dax
    phase: str = "NORMAL"  # NORMAL, RETRY_VALIDATE, FORMAT, FAILED
    retry_count: int = 0
    max_retries: int = 2
    initial_execution_failed: bool = False  # True if the first DAX execution attempt failed
    last_execution_error: Optional[str] = None  # Error from failed execution for retry context
    failed_dax_queries: List[str] = field(default_factory=list)  # Track failed DAX for context
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary."""
        return {
            "user_query": self.user_query,
            "intent": self.intent,
            "confidence": self.confidence,
            "generated_dax": self.generated_dax,
            "final_dax": self.final_dax,
            "is_valid": self.is_valid,
            "validation_issues": self.validation_issues,
            "execution_success": self.execution_success,
            "row_count": self.row_count,
            "steps_completed": self.steps_completed,
            "error": self.error,
            "step_timings": self.step_timings,
        }


# Thread-local state — each thread gets its own DAXWorkflowState.
# This is critical for parallel cross-domain execution where two analyst
# workflows run concurrently in separate threads.
import threading

_thread_local = threading.local()


def get_workflow_state() -> DAXWorkflowState:
    """Get the current thread's workflow state."""
    state = getattr(_thread_local, "workflow_state", None)
    if state is None:
        state = DAXWorkflowState()
        _thread_local.workflow_state = state
    return state


def set_workflow_state(state: DAXWorkflowState) -> None:
    """Set the workflow state for the current thread."""
    _thread_local.workflow_state = state


def reset_workflow_state() -> DAXWorkflowState:
    """Reset the workflow state for a new query (current thread only)."""
    old_state = getattr(_thread_local, "workflow_state", None)
    # Preserve conversation history
    history = old_state.conversation_history if old_state else []
    new_state = DAXWorkflowState(conversation_history=history)
    _thread_local.workflow_state = new_state
    return new_state
