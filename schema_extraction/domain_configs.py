# schema_extraction/domain_configs.py
"""
Domain Configurations — single source of truth for all data domains.

Defines for each domain:
  - fact_tables: root fact table(s) — connected dims are auto-discovered
  - extra_tables: additional tables to always include
  - measure_folders: which measure folders belong to this domain
  - output_prefix: filename prefix for output files (also used to derive schema_file path)
  - label / description: header text in the output schema file
  - planner_description: human-readable summary used by the LLM planner for routing

To add a new domain, simply add an entry here. The LLM planner, analyst
workflows, schema splitting, and schema loading will pick it up automatically.
"""

from typing import Dict, Any, List


DOMAIN_CONFIGS: List[Dict[str, Any]] = [
    {
        "name": "work_orders",
        "label": "WORK ORDERS SCHEMA",
        "description": (
            "This schema contains only tables, relationships, and measures\n"
            "related to fact_work_orders (any type of maintenance work)."
        ),
        "planner_description": "Work orders, maintenance tasks, repairs, inspections, SLA compliance, resolution time, contractors, asset maintenance",
        "fact_tables": ["fact_work_orders"],
        "extra_tables": [],              # additional tables to always include
        "measure_folders": ["RESOLUTION_METRICS"],
        "output_prefix": "schema_work_orders",
    },
    {
        "name": "citizen_complaints",
        "label": "COMPLAINTS SCHEMA",
        "description": (
            "This schema contains only tables, relationships, and measures\n"
            "related to fact_citizen_complaints (citizen complaints)."
        ),
        "planner_description": "Citizen complaints, public grievances, complaint categories, resolution status, response time, service channels",
        "fact_tables": ["fact_citizen_complaints"],
        "extra_tables": [],
        "measure_folders": [],
        "output_prefix": "schema_complaints",
    },
    {
        "name": "maintenance_costs",
        "label": "MAINTENANCE COSTS SCHEMA",
        "description": (
            "This schema contains only tables, relationships, and measures\n"
            "related to fact_maintenance_costs."
        ),
        "planner_description": "Maintenance costs, budgets, expenditure, cost breakdowns by asset, department, region, and contractor",
        "fact_tables": ["fact_maintenance_costs"],
        "extra_tables": [],              # additional tables to always include
        "measure_folders": [],
        "output_prefix": "schema_maintenance_costs",
    },
    {
        "name": "downtime",
        "label": "DOWNTIME SCHEMA",
        "description": (
            "This schema contains only tables, relationships, and measures\n"
            "related to fact_asset_downtime."
        ),
        "planner_description": "Asset downtime, outages, availability, mean time to repair (MTTR), downtime causes, impact assessment",
        "fact_tables": ["fact_asset_downtime"],
        "extra_tables": [],              # additional tables to always include
        "measure_folders": [],
        "output_prefix": "schema_asset_downtime",
    },
    # To add a new domain:
    # {
    #     "name": "new_domain",
    #     "label": "NEW DOMAIN SCHEMA",
    #     "description": "This schema contains tables related to ...",
    #     "planner_description": "What this domain covers — be specific so the LLM planner can route correctly",
    #     "fact_tables": ["factnewtable"],
    #     "extra_tables": [],
    #     "measure_folders": ["NewDomain"],
    #     "output_prefix": "schema_new_domain",
    # },
]


# ============================================================
# DOMAIN_REGISTRY — derived from DOMAIN_CONFIGS
# ============================================================
# This dict is consumed by the query planner and agent workflow.
# It maps domain name → {"description": ..., "schema_file": ...}
# ============================================================

DOMAIN_REGISTRY: Dict[str, Dict[str, str]] = {
    config["name"]: {
        "description": config["planner_description"],
        "schema_file": f"cache/schema/{config['output_prefix']}.txt",
    }
    for config in DOMAIN_CONFIGS
}


def get_domain_config(name: str) -> Dict[str, Any]:
    """Look up a domain config by name. Raises KeyError if not found."""
    for cfg in DOMAIN_CONFIGS:
        if cfg["name"] == name:
            return cfg
    raise KeyError(f"Unknown domain: '{name}'. Available: {[c['name'] for c in DOMAIN_CONFIGS]}")
