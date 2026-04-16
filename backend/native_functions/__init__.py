# backend/native_functions/__init__.py
"""
Native Functions — parameterized DAX templates that bypass LLM generation.

For specific, well-known query patterns, native functions provide:
  - Deterministic DAX output (no LLM hallucination)
  - Faster execution (skip LLM DAX generation)
  - Consistent results for standard KPI queries
"""

from backend.native_functions.registry import NativeFunction, NATIVE_FUNCTIONS
from backend.native_functions.matcher import match_native_function, resolve_native_function
