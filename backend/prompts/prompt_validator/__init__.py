# backend/prompts/prompt_validator/__init__.py
"""
DAX Validator Prompts

Global validation prompt for DAX query validation.
"""

from .dax_validator_global_instructions import DAX_VALIDATOR_PROMPT

__all__ = [
    'DAX_VALIDATOR_PROMPT',
]
