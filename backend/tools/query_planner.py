# backend/tools/query_planner.py
"""
TOOL: LLM Query Planner

Decomposes a user question into an ordered execution plan of
domain-specific sub-queries using an LLM call.

Replaces the old keyword-based IntentExtractor and all regex-based
cross-domain detection / query rephrasing functions.

Usage:
    from backend.tools.query_planner import QueryPlanner

    planner = QueryPlanner()
    plan = planner.plan("CSAT of the entity with the highest transactions")
    # plan.steps = [
    #   PlanStep(id=1, domain="transactions", query="Which entity has the highest transactions?", depends_on=None),
    #   PlanStep(id=2, domain="feedback", query="What is the CSAT for that entity?", depends_on=1),
    # ]
"""

import json
import asyncio
import time
import warnings
import logging
import concurrent.futures
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from semantic_kernel import Kernel
from semantic_kernel.contents.chat_history import ChatHistory

from backend.tools.auth import create_chat_service, get_llm_provider
from schema_extraction.domain_configs import DOMAIN_REGISTRY
from backend.prompts.query_planner_prompt import build_planner_prompt
from backend.executors.workflow_state import ConversationTurn

# Suppress httpx noise
warnings.filterwarnings("ignore", message=".*Event loop is closed.*")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────

@dataclass
class PlanStep:
    """A single step in the execution plan."""
    id: int
    domain: str
    query: str
    depends_on: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "domain": self.domain,
            "query": self.query,
            "depends_on": self.depends_on,
        }


@dataclass
class ExecutionPlan:
    """The full execution plan returned by the planner."""
    steps: List[PlanStep] = field(default_factory=list)
    planner_elapsed: float = 0.0
    raw_response: Optional[str] = None
    error: Optional[str] = None
    
    # Clarification support
    clarification_needed: bool = False
    clarification_message: Optional[str] = None
    clarification_suggestions: List[str] = field(default_factory=list)

    @property
    def is_cross_domain(self) -> bool:
        domains = {s.domain for s in self.steps}
        return len(domains) > 1

    @property
    def has_dependencies(self) -> bool:
        return any(s.depends_on is not None for s in self.steps)

    @property
    def domains(self) -> List[str]:
        return list(dict.fromkeys(s.domain for s in self.steps))  # preserve order, deduplicate

    def to_dict(self) -> dict:
        d = {
            "steps": [s.to_dict() for s in self.steps],
            "is_cross_domain": self.is_cross_domain,
            "has_dependencies": self.has_dependencies,
            "planner_elapsed": self.planner_elapsed,
        }
        if self.clarification_needed:
            d["clarification_needed"] = True
            d["clarification_message"] = self.clarification_message
            d["clarification_suggestions"] = self.clarification_suggestions
        return d


# ── Shared LLM service (singleton) ───────────────────────────

_planner_chat_service = None
_planner_settings_class = None
_planner_provider_name = None


def _get_planner_chat_service():
    """Get or create the shared planner LLM service."""
    global _planner_chat_service, _planner_settings_class, _planner_provider_name
    if _planner_chat_service is None:
        _planner_chat_service, _planner_settings_class, _planner_provider_name = create_chat_service(
            service_id="query_planner"
        )
        print(f"[LLM] Query Planner using provider: {_planner_provider_name}")
    return _planner_chat_service


# ── QueryPlanner class ────────────────────────────────────────

class QueryPlanner:
    """
    LLM-based query planner that decomposes user questions into
    domain-specific execution plans.
    """

    def __init__(self, domain_registry: Dict[str, Dict[str, str]] = None):
        self._domain_registry = domain_registry or DOMAIN_REGISTRY
        self._chat_service = _get_planner_chat_service()
        self._settings_class = _planner_settings_class
        self._provider_name = _planner_provider_name
        self._system_prompt = build_planner_prompt(self._domain_registry)
        self.kernel = Kernel()
        self.kernel.add_service(self._chat_service)
        print(f"[OK] Query Planner initialized (provider: {self._provider_name})")

    async def _plan_async(self, user_query: str, conversation_history: List[ConversationTurn] = None) -> ExecutionPlan:
        """Async implementation of the planner LLM call."""
        t0 = time.time()

        chat_history = ChatHistory(system_message=self._system_prompt)

        # Build the user message with conversation context for follow-ups
        user_message = user_query
        if conversation_history:
            context_parts = []
            for turn in conversation_history[-5:]:
                context_parts.append(turn.to_context_string())
            if context_parts:
                context_block = "\n---\n".join(context_parts)
                user_message = (
                    f"PREVIOUS CONVERSATION:\n{context_block}\n\n"
                    f"CURRENT QUESTION (may be a follow-up to the above — resolve references like "
                    f"'the above Entity', 'this service', 'that', etc. using the conversation context):\n"
                    f"{user_query}"
                )

        chat_history.add_user_message(user_message)

        # Use low reasoning effort for fast planning
        if self._provider_name == "compass":
            settings = self._settings_class(
                max_completion_tokens=1000,
                temperature=0.0,
            )
        else:
            settings = self._settings_class(
                max_completion_tokens=1000,
                extra_body={"reasoning_effort": "low"},
            )

        try:
            response = await self._chat_service.get_chat_message_content(
                chat_history=chat_history,
                settings=settings,
            )
            raw = str(response).strip()
            elapsed = time.time() - t0
            print(f"[PLANNER] LLM response ({elapsed:.2f}s): {raw[:300]}")
        except Exception as e:
            elapsed = time.time() - t0
            logger.error(f"Planner LLM call failed: {e}")
            return self._fallback_plan(user_query, elapsed, str(e))

        return self._parse_plan(raw, user_query, elapsed)

    def _parse_plan(self, raw: str, user_query: str, elapsed: float) -> ExecutionPlan:
        """Parse the LLM JSON response into an ExecutionPlan."""
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            # Remove ```json ... ``` wrappers
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"Planner returned invalid JSON: {e}. Raw: {raw[:500]}")
            return self._fallback_plan(user_query, elapsed, f"Invalid JSON: {e}")

        # Check if the planner returned a clarification request
        if data.get("clarification_needed"):
            suggestions = data.get("suggestions", [])
            message = data.get("message", "Could you clarify your question?")
            print(f"[PLANNER] Clarification needed: {message}")
            print(f"[PLANNER]   Suggestions: {suggestions}")
            return ExecutionPlan(
                planner_elapsed=elapsed,
                raw_response=raw,
                clarification_needed=True,
                clarification_message=message,
                clarification_suggestions=suggestions,
            )

        steps_raw = data.get("steps", [])
        if not steps_raw:
            return self._fallback_plan(user_query, elapsed, "Empty steps array")

        steps = []
        for s in steps_raw:
            domain = s.get("domain", "").lower()
            if domain not in self._domain_registry:
                logger.warning(f"Planner returned unknown domain '{domain}', skipping step")
                continue
            steps.append(PlanStep(
                id=s.get("id", len(steps) + 1),
                domain=domain,
                query=s.get("query", user_query),
                depends_on=s.get("depends_on"),
            ))

        if not steps:
            return self._fallback_plan(user_query, elapsed, "No valid steps after filtering")

        return ExecutionPlan(
            steps=steps,
            planner_elapsed=elapsed,
            raw_response=raw,
        )

    def _fallback_plan(self, user_query: str, elapsed: float, error: str) -> ExecutionPlan:
        """
        Fallback: single-step plan targeting the first registered domain.
        Ensures the system never fully fails due to planner issues.
        """
        default_domain = list(self._domain_registry.keys())[0]
        logger.warning(f"Planner fallback → {default_domain}: {error}")
        print(f"[PLANNER] Fallback → single-step '{default_domain}' plan: {error}")
        return ExecutionPlan(
            steps=[PlanStep(id=1, domain=default_domain, query=user_query, depends_on=None)],
            planner_elapsed=elapsed,
            error=error,
        )

    def plan(self, user_query: str, conversation_history: List[ConversationTurn] = None) -> ExecutionPlan:
        """
        Synchronous entry point: plan an execution for the given user query.

        Returns:
            ExecutionPlan with ordered steps
        """
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._plan_async(user_query, conversation_history))
            finally:
                loop.close()

        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(_run)
                return future.result(timeout=30)
        except concurrent.futures.TimeoutError:
            return self._fallback_plan(user_query, 30.0, "Planner timed out")
        except Exception as e:
            return self._fallback_plan(user_query, 0.0, str(e))


# ── Singleton accessor ────────────────────────────────────────

_planner_instance: Optional[QueryPlanner] = None


def get_planner() -> QueryPlanner:
    """Get or create the singleton QueryPlanner instance."""
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = QueryPlanner()
    return _planner_instance
