# backend/native_functions/registry.py
"""
Native Function Registry — parameterized DAX templates.

Each NativeFunction defines:
  - name:        unique identifier
  - domain:      which domain this function belongs to (must match DOMAIN_REGISTRY)
  - description: human-readable description (used by the LLM matcher)
  - parameters:  list of parameter definitions (name, type, description, required, default)
  - dax_template: DAX query string with {param_name} placeholders
  - notes:       explanation of what the query does (returned alongside results)

To add a new native function, add a NativeFunction instance to NATIVE_FUNCTIONS.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class NativeFunctionParameter:
    """Definition of a single parameter for a native function."""
    name: str
    type: str  # "string", "integer", "date", "list"
    description: str
    required: bool = True
    default: Optional[Any] = None
    enum: Optional[List[str]] = None  # Allowed values (if restricted)


@dataclass
class NativeFunction:
    """A parameterized DAX template that bypasses LLM generation."""
    name: str
    domain: str
    description: str
    parameters: List[NativeFunctionParameter]
    dax_template: str
    notes: str = ""
    examples: List[str] = field(default_factory=list)  # Example user questions that should match
    # Tables/columns/measures referenced (for traceability)
    used_tables: List[str] = field(default_factory=list)
    used_columns: List[str] = field(default_factory=list)
    used_measures: List[str] = field(default_factory=list)

    def render(self, params: Dict[str, Any]) -> str:
        """
        Fill in the DAX template with the provided parameters.

        Args:
            params: Dict mapping parameter names to values.

        Returns:
            The final DAX query string.

        Raises:
            ValueError: If a required parameter is missing.
        """
        # Build the full parameter dict with defaults
        resolved = {}
        for p in self.parameters:
            if p.name in params:
                resolved[p.name] = params[p.name]
            elif p.default is not None:
                resolved[p.name] = p.default
            elif p.required:
                raise ValueError(f"Missing required parameter: {p.name}")

        return self.dax_template.format(**resolved)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for LLM matching prompt."""
        return {
            "name": self.name,
            "domain": self.domain,
            "description": self.description,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default,
                    **({"enum": p.enum} if p.enum else {}),
                }
                for p in self.parameters
            ],
        }


# ============================================================
# NATIVE FUNCTION CATALOG
# ============================================================
# Add new native functions here. They will be automatically
# picked up by the matcher.
# ============================================================

NATIVE_FUNCTIONS: List[NativeFunction] = [

    # ==========================================================
    # WORK ORDERS DOMAIN
    # ==========================================================

    # ----------------------------------------------------------
    # Work Orders: Count by type over time
    # ----------------------------------------------------------
    NativeFunction(
        name="work_order_count_by_type",
        domain="work_orders",
        description="Work order count breakdown by work order type, optionally filtered by region and/or time period. Use for any question about how many work orders exist per type.",
        examples=[
            "How many work orders by type?",
            "Show work order count by type in Q1 2025",
            "Work order breakdown by maintenance type",
            "How many corrective vs preventive work orders?",
            "Work order volume by type for Abu Dhabi City",
        ],
        parameters=[
            NativeFunctionParameter(
                name="region_filter",
                type="string",
                description="Region name to filter by (e.g. 'Abu Dhabi City', 'Al Ain', 'Al Dhafra'). Leave empty for all regions.",
                required=False,
                default="",
            ),
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter (e.g. 2025). Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="quarter",
                type="integer",
                description="Quarter (1-4). Leave 0 for no quarter filter.",
                required=False,
                default=0,
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    {region_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'fact_work_orders'[work_order_type],
            {date_filter_ref}
            {region_filter_ref}
            "Work Order Count", COUNTROWS('fact_work_orders')
        )

EVALUATE
    __Core
ORDER BY
    [Work Order Count] DESC""",
        notes="Native function: Work order count breakdown by type.",
        used_tables=["fact_work_orders", "dim_date", "dim_region"],
        used_columns=["fact_work_orders[work_order_type]"],
        used_measures=[],
    ),

    # ----------------------------------------------------------
    # Work Orders: SLA breach rate by region
    # ----------------------------------------------------------
    NativeFunction(
        name="sla_breach_rate_by_region",
        domain="work_orders",
        description="SLA breach rate by region (or overall), optionally filtered by time period. Use for any question about SLA compliance, SLA breaches, or SLA performance across regions.",
        examples=[
            "What is the SLA breach rate by region?",
            "SLA performance by region",
            "Which region has the most SLA breaches?",
            "Show SLA compliance rate",
            "SLA breach rate for Q1 2025",
            "How many work orders breached SLA?",
        ],
        parameters=[
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter (e.g. 2025). Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="quarter",
                type="integer",
                description="Quarter (1-4). Leave 0 for no quarter filter.",
                required=False,
                default=0,
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dim_region'[region_name],
            {date_filter_ref}
            "Total Work Orders", COUNTROWS('fact_work_orders'),
            "SLA Breached", CALCULATE(COUNTROWS('fact_work_orders'), 'fact_work_orders'[sla_breached_flag] = TRUE),
            "SLA Breach Rate_Value", DIVIDE(CALCULATE(COUNTROWS('fact_work_orders'), 'fact_work_orders'[sla_breached_flag] = TRUE), COUNTROWS('fact_work_orders'))
        )

    VAR __Result =
        ADDCOLUMNS(
            __Core,
            "SLA Breach Rate", FORMAT([SLA Breach Rate_Value], "0.0%")
        )

EVALUATE
    __Result
ORDER BY
    [SLA Breach Rate_Value] DESC""",
        notes="Native function: SLA breach rate by region.",
        used_tables=["fact_work_orders", "dim_region", "dim_date"],
        used_columns=["dim_region[region_name]", "fact_work_orders[sla_breached_flag]"],
        used_measures=[],
    ),

    # ----------------------------------------------------------
    # Work Orders: Monthly trend
    # ----------------------------------------------------------
    NativeFunction(
        name="work_order_monthly_trend",
        domain="work_orders",
        description="Work order count trend over the last N months. Use for any question about work order volume trends over time.",
        examples=[
            "Show me work order trend for the last 6 months",
            "Monthly work order count",
            "How has work order volume changed over time?",
            "Work order trend last 3 months",
            "Work orders per month",
        ],
        parameters=[
            NativeFunctionParameter(
                name="n_months",
                type="integer",
                description="Number of completed months to look back",
                required=False,
                default=6,
            ),
            NativeFunctionParameter(
                name="region_filter",
                type="string",
                description="Region name to filter by. Leave empty for all regions.",
                required=False,
                default="",
            ),
        ],
        dax_template="""DEFINE
    VAR __ReferenceDate = TODAY()
    VAR __StartRange = EOMONTH(__ReferenceDate, -{n_months}) + 1
    VAR __EndRange = EOMONTH(__ReferenceDate, -1)

    VAR __DateFilter =
        FILTER(
            ALL('dim_date'),
            'dim_date'[date_key] >= INT(FORMAT(__StartRange, "YYYYMMDD")) &&
            'dim_date'[date_key] <= INT(FORMAT(__EndRange, "YYYYMMDD"))
        )

    {region_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dim_date'[year],
            'dim_date'[month_number],
            'dim_date'[month_name],
            __DateFilter,
            {region_filter_ref}
            "Work Order Count", COUNTROWS('fact_work_orders')
        )

EVALUATE
    __Core
ORDER BY
    'dim_date'[year] ASC,
    'dim_date'[month_number] ASC""",
        notes="Native function: Work order monthly trend over last N months.",
        used_tables=["fact_work_orders", "dim_date", "dim_region"],
        used_columns=["dim_date[year]", "dim_date[month_number]", "dim_date[month_name]"],
        used_measures=[],
    ),

    # ----------------------------------------------------------
    # Work Orders: Top contractors by volume
    # ----------------------------------------------------------
    NativeFunction(
        name="top_contractors_by_work_orders",
        domain="work_orders",
        description="Top N contractors ranked by number of work orders assigned. Use for any question about contractor performance, top contractors, or contractor work load.",
        examples=[
            "Top 5 contractors by work order volume",
            "Which contractor has the most work orders?",
            "Contractor ranking by work orders",
            "Show top contractors",
            "Busiest contractors",
        ],
        parameters=[
            NativeFunctionParameter(
                name="top_n",
                type="integer",
                description="Number of top contractors to return",
                required=False,
                default=5,
            ),
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter (e.g. 2025). Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dim_contractor'[contractor_name],
            {date_filter_ref}
            "Work Order Count", COUNTROWS('fact_work_orders')
        )

    VAR __Result = TOPN({top_n}, __Core, [Work Order Count], DESC)

EVALUATE
    __Result
ORDER BY
    [Work Order Count] DESC""",
        notes="Native function: Top contractors by work order volume.",
        used_tables=["fact_work_orders", "dim_contractor", "dim_date"],
        used_columns=["dim_contractor[contractor_name]"],
        used_measures=[],
    ),

    # ==========================================================
    # MAINTENANCE COSTS DOMAIN
    # ==========================================================

    # ----------------------------------------------------------
    # Maintenance Costs: Total cost by entity
    # ----------------------------------------------------------
    NativeFunction(
        name="maintenance_cost_by_entity",
        domain="maintenance_costs",
        description="Total maintenance cost breakdown by entity, optionally filtered by time period. Use for questions about overall maintenance spending, cost by entity, or total cost.",
        examples=[
            "What is the total maintenance cost by entity?",
            "Maintenance spending breakdown",
            "Which entity spends the most on maintenance?",
            "Total cost by entity for Q1 2025",
            "Show maintenance costs per entity",
        ],
        parameters=[
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter. Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="quarter",
                type="integer",
                description="Quarter (1-4). Leave 0 for no quarter filter.",
                required=False,
                default=0,
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dim_entity'[entity_name],
            {date_filter_ref}
            "Total Cost (AED)", SUM('fact_maintenance_costs'[total_cost_aed]),
            "Labor Cost (AED)", SUM('fact_maintenance_costs'[labor_cost_aed]),
            "Materials Cost (AED)", SUM('fact_maintenance_costs'[materials_cost_aed]),
            "Equipment Cost (AED)", SUM('fact_maintenance_costs'[equipment_cost_aed])
        )

EVALUATE
    __Core
ORDER BY
    [Total Cost (AED)] DESC""",
        notes="Native function: Total maintenance cost breakdown by entity.",
        used_tables=["fact_maintenance_costs", "dim_entity", "dim_date"],
        used_columns=["dim_entity[entity_name]", "fact_maintenance_costs[total_cost_aed]", "fact_maintenance_costs[labor_cost_aed]", "fact_maintenance_costs[materials_cost_aed]", "fact_maintenance_costs[equipment_cost_aed]"],
        used_measures=[],
    ),

    # ----------------------------------------------------------
    # Maintenance Costs: Cost comparison between two quarters
    # ----------------------------------------------------------
    NativeFunction(
        name="maintenance_cost_quarter_comparison",
        domain="maintenance_costs",
        description="Compare total maintenance costs between two quarters. Use for questions about cost changes between periods, quarter-over-quarter comparison, or cost trends.",
        examples=[
            "How do maintenance costs compare between Q1 and Q2 2025?",
            "Compare Q1 and Q2 maintenance costs",
            "Maintenance cost change from Q1 to Q2",
            "Quarter over quarter cost comparison",
            "Cost difference between Q3 and Q4 2025",
        ],
        parameters=[
            NativeFunctionParameter(
                name="year1",
                type="integer",
                description="Year of the first (baseline) quarter",
                required=True,
            ),
            NativeFunctionParameter(
                name="quarter1",
                type="integer",
                description="First quarter number (1-4)",
                required=True,
            ),
            NativeFunctionParameter(
                name="year2",
                type="integer",
                description="Year of the second (comparison) quarter",
                required=True,
            ),
            NativeFunctionParameter(
                name="quarter2",
                type="integer",
                description="Second quarter number (1-4)",
                required=True,
            ),
        ],
        dax_template="""DEFINE
    VAR __Period1 =
        SUMMARIZECOLUMNS(
            'dim_entity'[entity_name],
            FILTER(ALL('dim_date'), 'dim_date'[year] = {year1} && 'dim_date'[quarter] = {quarter1}),
            "Cost_Q1", SUM('fact_maintenance_costs'[total_cost_aed])
        )

    VAR __Period2 =
        SUMMARIZECOLUMNS(
            'dim_entity'[entity_name],
            FILTER(ALL('dim_date'), 'dim_date'[year] = {year2} && 'dim_date'[quarter] = {quarter2}),
            "Cost_Q2", SUM('fact_maintenance_costs'[total_cost_aed])
        )

    VAR __Joined = NATURALINNERJOIN(__Period1, __Period2)

    VAR __Result =
        ADDCOLUMNS(
            __Joined,
            "Change (AED)", [Cost_Q2] - [Cost_Q1],
            "Change %", FORMAT(DIVIDE([Cost_Q2] - [Cost_Q1], [Cost_Q1]), "0.0%")
        )

EVALUATE
    __Result
ORDER BY
    [Change (AED)] DESC""",
        notes="Native function: Quarter-over-quarter maintenance cost comparison by entity.",
        used_tables=["fact_maintenance_costs", "dim_entity", "dim_date"],
        used_columns=["dim_entity[entity_name]", "dim_date[year]", "dim_date[quarter]", "fact_maintenance_costs[total_cost_aed]"],
        used_measures=[],
    ),

    # ----------------------------------------------------------
    # Maintenance Costs: Cost by work order type
    # ----------------------------------------------------------
    NativeFunction(
        name="maintenance_cost_by_work_order_type",
        domain="maintenance_costs",
        description="Maintenance cost breakdown by work order type. Use for questions about cost per work type (corrective, preventive, etc.).",
        examples=[
            "What is the maintenance cost by work order type?",
            "Cost of corrective vs preventive maintenance",
            "Spending by maintenance type",
            "How much does each type of work order cost?",
        ],
        parameters=[
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter. Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="quarter",
                type="integer",
                description="Quarter (1-4). Leave 0 for no quarter filter.",
                required=False,
                default=0,
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'fact_maintenance_costs'[work_order_type],
            {date_filter_ref}
            "Total Cost (AED)", SUM('fact_maintenance_costs'[total_cost_aed]),
            "Labor Hours", SUM('fact_maintenance_costs'[labor_hours]),
            "Avg Cost per WO", DIVIDE(SUM('fact_maintenance_costs'[total_cost_aed]), COUNTROWS('fact_maintenance_costs'))
        )

EVALUATE
    __Core
ORDER BY
    [Total Cost (AED)] DESC""",
        notes="Native function: Maintenance cost by work order type.",
        used_tables=["fact_maintenance_costs", "dim_date"],
        used_columns=["fact_maintenance_costs[work_order_type]", "fact_maintenance_costs[total_cost_aed]", "fact_maintenance_costs[labor_hours]"],
        used_measures=[],
    ),

    # ==========================================================
    # DOWNTIME DOMAIN
    # ==========================================================

    # ----------------------------------------------------------
    # Downtime: Top assets by downtime hours
    # ----------------------------------------------------------
    NativeFunction(
        name="top_assets_by_downtime",
        domain="downtime",
        description="Top N assets with the most downtime hours. Use for questions about which assets have the most outages, longest downtime, or worst-performing assets.",
        examples=[
            "Which assets have the most downtime?",
            "Top 10 assets by downtime hours",
            "Assets with longest outages",
            "Worst performing assets by downtime",
            "Show me assets with the highest downtime",
        ],
        parameters=[
            NativeFunctionParameter(
                name="top_n",
                type="integer",
                description="Number of top assets to return",
                required=False,
                default=10,
            ),
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter. Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dim_asset'[asset_name],
            'dim_asset_category'[asset_category_name],
            {date_filter_ref}
            "Total Downtime Hours", SUM('fact_asset_downtime'[downtime_hours]),
            "Downtime Events", COUNTROWS('fact_asset_downtime')
        )

    VAR __Result = TOPN({top_n}, __Core, [Total Downtime Hours], DESC)

EVALUATE
    __Result
ORDER BY
    [Total Downtime Hours] DESC""",
        notes="Native function: Top assets by total downtime hours.",
        used_tables=["fact_asset_downtime", "dim_asset", "dim_asset_category", "dim_date"],
        used_columns=["dim_asset[asset_name]", "dim_asset_category[asset_category_name]", "fact_asset_downtime[downtime_hours]"],
        used_measures=[],
    ),

    # ----------------------------------------------------------
    # Downtime: Events by reason
    # ----------------------------------------------------------
    NativeFunction(
        name="downtime_by_reason",
        domain="downtime",
        description="Downtime event count and total hours grouped by downtime reason. Use for questions about why assets are going offline, causes of downtime, or downtime reason breakdown.",
        examples=[
            "What are the main reasons for asset downtime?",
            "Downtime by reason",
            "Why are assets going offline?",
            "Show downtime causes",
            "Breakdown of downtime reasons",
        ],
        parameters=[
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter. Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="region_filter",
                type="string",
                description="Region name to filter by. Leave empty for all regions.",
                required=False,
                default="",
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    {region_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'fact_asset_downtime'[downtime_reason],
            {date_filter_ref}
            {region_filter_ref}
            "Total Downtime Hours", SUM('fact_asset_downtime'[downtime_hours]),
            "Event Count", COUNTROWS('fact_asset_downtime')
        )

EVALUATE
    __Core
ORDER BY
    [Total Downtime Hours] DESC""",
        notes="Native function: Downtime breakdown by reason.",
        used_tables=["fact_asset_downtime", "dim_date", "dim_region"],
        used_columns=["fact_asset_downtime[downtime_reason]", "fact_asset_downtime[downtime_hours]"],
        used_measures=[],
    ),

    # ----------------------------------------------------------
    # Downtime: Critical public impact events
    # ----------------------------------------------------------
    NativeFunction(
        name="downtime_high_public_impact",
        domain="downtime",
        description="Downtime events with High or Critical public impact. Use for questions about critical outages, public-impact events, or high-severity downtime.",
        examples=[
            "How many downtime events had critical public impact?",
            "Show high impact downtime events",
            "Critical public impact outages",
            "Downtime events affecting the public",
            "High severity downtime count",
        ],
        parameters=[
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter. Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="impact_levels",
                type="string",
                description="Comma-separated impact levels to include (e.g. 'High,Critical'). Default is 'High,Critical'.",
                required=False,
                default="High,Critical",
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    VAR __ImpactFilter =
        FILTER(
            ALL('fact_asset_downtime'[public_impact]),
            'fact_asset_downtime'[public_impact] IN {{{impact_levels_formatted}}}
        )

    VAR __Core =
        SUMMARIZECOLUMNS(
            'fact_asset_downtime'[public_impact],
            'dim_region'[region_name],
            {date_filter_ref}
            __ImpactFilter,
            "Total Downtime Hours", SUM('fact_asset_downtime'[downtime_hours]),
            "Event Count", COUNTROWS('fact_asset_downtime')
        )

EVALUATE
    __Core
ORDER BY
    [Event Count] DESC""",
        notes="Native function: Downtime events with high/critical public impact by region.",
        used_tables=["fact_asset_downtime", "dim_region", "dim_date"],
        used_columns=["fact_asset_downtime[public_impact]", "dim_region[region_name]", "fact_asset_downtime[downtime_hours]"],
        used_measures=[],
    ),

    # ==========================================================
    # CITIZEN COMPLAINTS DOMAIN
    # ==========================================================

    # ----------------------------------------------------------
    # Citizen Complaints: Monthly complaint trend
    # ----------------------------------------------------------
    NativeFunction(
        name="complaint_monthly_trend",
        domain="citizen_complaints",
        description="Citizen complaint count trend over the last N months. Use for questions about complaint volume trends, monthly complaints, or how complaints are changing over time.",
        examples=[
            "Show monthly complaint trends",
            "Complaint count over last 6 months",
            "How are complaints trending?",
            "Monthly complaint volume",
            "Complaint trend for 2025",
        ],
        parameters=[
            NativeFunctionParameter(
                name="n_months",
                type="integer",
                description="Number of completed months to look back",
                required=False,
                default=6,
            ),
            NativeFunctionParameter(
                name="region_filter",
                type="string",
                description="Region name to filter by. Leave empty for all regions.",
                required=False,
                default="",
            ),
        ],
        dax_template="""DEFINE
    VAR __ReferenceDate = TODAY()
    VAR __StartRange = EOMONTH(__ReferenceDate, -{n_months}) + 1
    VAR __EndRange = EOMONTH(__ReferenceDate, -1)

    VAR __DateFilter =
        FILTER(
            ALL('dim_date'),
            'dim_date'[date_key] >= INT(FORMAT(__StartRange, "YYYYMMDD")) &&
            'dim_date'[date_key] <= INT(FORMAT(__EndRange, "YYYYMMDD"))
        )

    {region_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dim_date'[year],
            'dim_date'[month_number],
            'dim_date'[month_name],
            __DateFilter,
            {region_filter_ref}
            "Complaint Count", COUNTROWS('fact_citizen_complaints'),
            "Avg Response Time (hrs)", AVERAGE('fact_citizen_complaints'[response_time_hours])
        )

EVALUATE
    __Core
ORDER BY
    'dim_date'[year] ASC,
    'dim_date'[month_number] ASC""",
        notes="Native function: Monthly complaint trend over last N months.",
        used_tables=["fact_citizen_complaints", "dim_date", "dim_region"],
        used_columns=["dim_date[year]", "dim_date[month_number]", "dim_date[month_name]", "fact_citizen_complaints[response_time_hours]"],
        used_measures=[],
    ),

    # ----------------------------------------------------------
    # Citizen Complaints: By complaint type
    # ----------------------------------------------------------
    NativeFunction(
        name="complaints_by_type",
        domain="citizen_complaints",
        description="Complaint count grouped by complaint type. Use for questions about what kinds of complaints are most common, complaint type breakdown, or top complaint categories.",
        examples=[
            "What are the most common complaint types?",
            "Complaints by type",
            "Top complaint categories",
            "What do citizens complain about the most?",
            "Complaint breakdown by issue type",
        ],
        parameters=[
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter. Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="quarter",
                type="integer",
                description="Quarter (1-4). Leave 0 for no quarter filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="region_filter",
                type="string",
                description="Region name to filter by. Leave empty for all regions.",
                required=False,
                default="",
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    {region_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'fact_citizen_complaints'[complaint_type],
            {date_filter_ref}
            {region_filter_ref}
            "Complaint Count", COUNTROWS('fact_citizen_complaints'),
            "Avg Satisfaction Score", AVERAGE('fact_citizen_complaints'[citizen_satisfaction_score]),
            "Avg Response Time (hrs)", AVERAGE('fact_citizen_complaints'[response_time_hours])
        )

EVALUATE
    __Core
ORDER BY
    [Complaint Count] DESC""",
        notes="Native function: Complaint count by complaint type.",
        used_tables=["fact_citizen_complaints", "dim_date", "dim_region"],
        used_columns=["fact_citizen_complaints[complaint_type]", "fact_citizen_complaints[citizen_satisfaction_score]", "fact_citizen_complaints[response_time_hours]"],
        used_measures=[],
    ),

    # ----------------------------------------------------------
    # Citizen Complaints: Satisfaction score by region
    # ----------------------------------------------------------
    NativeFunction(
        name="complaint_satisfaction_by_region",
        domain="citizen_complaints",
        description="Average citizen satisfaction score by region. Use for questions about citizen satisfaction, happiness scores, or regional service quality.",
        examples=[
            "What is the citizen satisfaction score by region?",
            "Satisfaction scores across regions",
            "Which region has the happiest citizens?",
            "Average satisfaction by region",
            "Citizen happiness rating by area",
        ],
        parameters=[
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter. Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="quarter",
                type="integer",
                description="Quarter (1-4). Leave 0 for no quarter filter.",
                required=False,
                default=0,
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dim_region'[region_name],
            {date_filter_ref}
            "Avg Satisfaction Score", AVERAGE('fact_citizen_complaints'[citizen_satisfaction_score]),
            "Total Complaints", COUNTROWS('fact_citizen_complaints'),
            "Avg Response Time (hrs)", AVERAGE('fact_citizen_complaints'[response_time_hours])
        )

EVALUATE
    __Core
ORDER BY
    [Avg Satisfaction Score] DESC""",
        notes="Native function: Citizen satisfaction score by region.",
        used_tables=["fact_citizen_complaints", "dim_region", "dim_date"],
        used_columns=["dim_region[region_name]", "fact_citizen_complaints[citizen_satisfaction_score]", "fact_citizen_complaints[response_time_hours]"],
        used_measures=[],
    ),

]
