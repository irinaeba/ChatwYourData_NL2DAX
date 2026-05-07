"""
Prompts Module

This package contains all system prompts used by the DAX generation tools.

Subpackages:
- prompt_generator: DAX generation prompts (per domain)
- prompt_validator: DAX validation prompt (global)
"""

from .prompt_generator import DAX_GENERATOR_PROMPT_WORK_ORDERS, DAX_GENERATOR_PROMPT_CITIZEN_COMPLAINTS, DAX_GENERATOR_PROMPT_MAINTENANCE_COSTS, DAX_GENERATOR_PROMPT_DOWNTIME
from .prompt_validator import DAX_VALIDATOR_PROMPT
from .answer_formatter_prompt import ANSWER_FORMATTER_PROMPT

__all__ = [
    'DAX_GENERATOR_PROMPT_WORK_ORDERS',
    'DAX_GENERATOR_PROMPT_CITIZEN_COMPLAINTS',
    'DAX_GENERATOR_PROMPT_MAINTENANCE_COSTS',
    'DAX_GENERATOR_PROMPT_DOWNTIME',
    'DAX_VALIDATOR_PROMPT',
    'ANSWER_FORMATTER_PROMPT',
]
