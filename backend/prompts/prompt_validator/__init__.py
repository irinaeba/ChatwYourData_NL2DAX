# backend/prompts/prompt_validator/__init__.py
"""
DAX Validator Prompts

Domain-specific system prompts for DAX query validation.
"""

from .dax_validator_prompt_transactions import DAX_VALIDATOR_PROMPT_TRANSACTIONS
from .dax_validator_prompt_feedback import DAX_VALIDATOR_PROMPT_FEEDBACK

__all__ = [
    'DAX_VALIDATOR_PROMPT_TRANSACTIONS',
    'DAX_VALIDATOR_PROMPT_FEEDBACK',
]
