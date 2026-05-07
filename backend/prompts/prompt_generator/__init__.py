# backend/prompts/prompt_generator/__init__.py
"""
DAX Generator Prompts

Domain-specific system prompts for DAX query generation.
"""

from .dax_generator_prompt_work_orders import DAX_GENERATOR_PROMPT_WORK_ORDERS
from .dax_generator_prompt_citizen_complaints import DAX_GENERATOR_PROMPT_CITIZEN_COMPLAINTS
from .dax_generator_prompt_maintenance_costs import DAX_GENERATOR_PROMPT_MAINTENANCE_COSTS
from .dax_generator_prompt_downtime import DAX_GENERATOR_PROMPT_DOWNTIME

__all__ = [
    'DAX_GENERATOR_PROMPT_WORK_ORDERS',
    'DAX_GENERATOR_PROMPT_CITIZEN_COMPLAINTS',
    'DAX_GENERATOR_PROMPT_MAINTENANCE_COSTS',
    'DAX_GENERATOR_PROMPT_DOWNTIME',
]
