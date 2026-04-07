# backend/prompts/domain_registry.py
"""
Domain Registry — single source of truth for all data domains.

Each domain maps to:
  - description: human-readable summary of what the domain covers (used by the LLM planner)
  - schema_file: path to the pre-filtered schema file for the DAX generator

To add a new domain, simply add an entry here. The LLM planner, analyst
workflows, and schema loading will pick it up automatically.

You must also add a matching entry in:
  - schema_extraction/domain_configs.py  (schema splitting rules)
  - backend/prompts/dax_generator_prompt_{domain}.py  (DAX generation prompt)
  - backend/prompts/dax_validator_prompt_{domain}.py   (DAX validation prompt)
"""

from typing import Dict

DOMAIN_REGISTRY: Dict[str, Dict[str, str]] = {
    "transactions": {
        "description": "Transaction volumes, services, applications, SLA compliance, completion time, application statuses, channels, service categories",
        "schema_file": "cache/schema/schema_transactions.txt",
    },
    "feedback": {
        "description": "Customer feedback: NPS (Net Promoter Score), CES (Customer Effort Score), CSAT (Customer Satisfaction), satisfaction ratings, promoters, detractors, passives, smiley types, survey types",
        "schema_file": "cache/schema/schema_feedback.txt",
    },
    "cases": {
      "description": "Contact Center cases, CRM cases, Case Number, Ticket Nmber, Case SLA: responsiveness score, aggregate score, CRM case SLA, Case CSAT: Customer Satisfaction for contact center, CRM",
      "schema_file": "cache/schema/schema_cases.txt",
    },
    "focus": {
      "description": "Shows the percent change in top 6 KPIs in the current week. To be used as areas of focus or executive summary.",
      "schema_file": "cache/schema/schema_focus.txt",
    },
    # To add a new domain:
    # "new_domain": {
    #     "description": "What this domain covers — be specific so the LLM planner can route correctly",
    #     "schema_file": "cache/schema/schema_new_domain.txt",
    # },
}
