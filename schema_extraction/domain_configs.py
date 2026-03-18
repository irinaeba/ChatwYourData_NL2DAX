# schema_extraction/domain_configs.py
"""
Domain Configurations for Schema Extraction

Defines the splitting rules for each data domain:
  - fact_tables: root fact table(s) — connected dims are auto-discovered
  - extra_tables: additional tables to always include
  - measure_folders: which measure folders belong to this domain
  - output_prefix: filename prefix for output files
  - label / description: header text in the output schema file

To add a new domain, add an entry here AND a matching entry in
  backend/prompts/domain_registry.py
(both lists must stay in sync).
"""

from typing import Dict, Any, List


DOMAIN_CONFIGS: List[Dict[str, Any]] = [
    {
        "name": "transactions",
        "label": "TRANSACTIONS SCHEMA",
        "description": (
            "This schema contains only tables, relationships, and measures\n"
            "related to FactTransactions (TAMM platform transactions)."
        ),
        "fact_tables": ["facttransactions"],
        "extra_tables": [],              # additional tables to always include
        "measure_folders": ["Transactions"],
        "output_prefix": "schema_transactions",
    },
    {
        "name": "feedback",
        "label": "FEEDBACK SCHEMA",
        "description": (
            "This schema contains only tables, relationships, and measures\n"
            "related to FactADFeedback (customer feedback / surveys)."
        ),
        "fact_tables": ["factadfeedback"],
        "extra_tables": ["tempdata"],     # user requested tempdata in feedback
        "measure_folders": ["Feedback"],
        "output_prefix": "schema_feedback",
    },
    # To add a new domain:
    # {
    #     "name": "new_domain",
    #     "label": "NEW DOMAIN SCHEMA",
    #     "description": "This schema contains tables related to ...",
    #     "fact_tables": ["factnewtable"],
    #     "extra_tables": [],
    #     "measure_folders": ["NewDomain"],
    #     "output_prefix": "schema_new_domain",
    # },
]


def get_domain_config(name: str) -> Dict[str, Any]:
    """Look up a domain config by name. Raises KeyError if not found."""
    for cfg in DOMAIN_CONFIGS:
        if cfg["name"] == name:
            return cfg
    raise KeyError(f"Unknown domain: '{name}'. Available: {[c['name'] for c in DOMAIN_CONFIGS]}")
