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

    # ----------------------------------------------------------
    # FEEDBACK: CSAT trend for last N months
    # ----------------------------------------------------------
    NativeFunction(
        name="csat_trend_last_n_months",
        domain="feedback",
        description="CSAT (Customer Satisfaction) trend over the last N completed months, optionally filtered by entity/ADGE. Use for any question about CSAT over time.",
        examples=[
            "Show me CSAT trend for the last 6 months",
            "What is the CSAT trend?",
            "Show the overall CSAT trend for all entities for the last 12 months",
            "CSAT over the last 3 months",
            "Customer satisfaction trend",
            "How has CSAT changed over time?",
            "Monthly CSAT for DOH last 6 months",
        ],
        parameters=[
            NativeFunctionParameter(
                name="n_months",
                type="integer",
                description="Number of completed months to look back",
                required=False,
                default=3,
            ),
            NativeFunctionParameter(
                name="entity_filter",
                type="string",
                description="Entity/ADGE short name to filter by (e.g. 'DOH', 'DED'). Leave empty for all entities.",
                required=False,
                default="",
            ),
        ],
        dax_template="""DEFINE
    VAR __ReferenceDate = TODAY()
    VAR __StartRange = EOMONTH(__ReferenceDate, -{n_months} - 1) + 1
    VAR __EndRange = EOMONTH(__ReferenceDate, -1)

    VAR __DateFilter =
        FILTER(
            ALL('dimdate'),
            'dimdate'[Date] >= __StartRange &&
            'dimdate'[Date] <= __EndRange
        )

    {entity_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dimdate'[Year],
            'dimdate'[Month],
            'dimdate'[MonthName],
            __DateFilter,
            {entity_filter_ref}
            "CSAT", FORMAT([Happy Feedback Percentage], "0.0%"),
            "Total Responses", [Total Feedback Responses Received]
        )

EVALUATE
    __Core
ORDER BY
    'dimdate'[Year] ASC,
    'dimdate'[Month] ASC""",
        notes="Native function: CSAT trend over last N completed months.",
        used_tables=["factadfeedback", "dimdate", "dimadge"],
        used_columns=["dimdate[Year]", "dimdate[Month]", "dimdate[MonthName]"],
        used_measures=["Happy Feedback Percentage", "Total Feedback Responses Received"],
    ),

    # ----------------------------------------------------------
    # FEEDBACK: NPS for a specific entity
    # ----------------------------------------------------------
    NativeFunction(
        name="nps_by_entity",
        domain="feedback",
        description="Net Promoter Score (NPS) for a specific entity/ADGE or all entities, optionally for a given year and month. Use for any question about NPS scores, promoters, or detractors.",
        examples=[
            "What is the NPS?",
            "Show NPS by entity",
            "NPS for DOH in March 2026",
            "What is the Net Promoter Score for all entities?",
            "Show me promoters and detractors",
            "NPS breakdown",
            "Which entity has the highest NPS?",
        ],
        parameters=[
            NativeFunctionParameter(
                name="entity_filter",
                type="string",
                description="Entity/ADGE short name (e.g. 'DOH', 'DED'). Leave empty for all entities.",
                required=False,
                default="",
            ),
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter (e.g. 2026). Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="month",
                type="integer",
                description="Month number (1-12). Leave 0 for no month filter.",
                required=False,
                default=0,
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    {entity_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dimadge'[ShortName],
            {date_filter_ref}
            {entity_filter_ref}
            "NPS", [Net Promoter Score],
            "Promoters %", FORMAT([Promotors Percentage], "0.0%"),
            "Detractors %", FORMAT([Detractors Percentage], "0.0%"),
            "Total Responses", [Total Feedback_NPS]
        )

    VAR __Filtered =
        FILTER(
            __Core,
            [Total Responses] >= 30
        )

EVALUATE
    __Filtered
ORDER BY
    [NPS] DESC""",
        notes="Native function: NPS breakdown by entity.",
        used_tables=["factadfeedback", "dimadge", "dimdate"],
        used_columns=["dimadge[ShortName]"],
        used_measures=["Net Promoter Score", "Promotors Percentage", "Detractors Percentage", "Total Feedback_NPS"],
    ),

    # ----------------------------------------------------------
    # FEEDBACK: CES (Customer Effort Score) by entity
    # ----------------------------------------------------------
    NativeFunction(
        name="ces_by_entity",
        domain="feedback",
        description="Customer Effort Score (CES) by entity/ADGE, optionally for a specific time period. Use for any question about CES, customer effort, or ease of service.",
        examples=[
            "What is the CES?",
            "Show CES by entity",
            "Customer Effort Score for all entities",
            "CES for DOH",
            "What is the customer effort score in March 2026?",
            "Show me CES scores",
            "Which entity has the best CES?",
            "How easy is it for customers?",
        ],
        parameters=[
            NativeFunctionParameter(
                name="entity_filter",
                type="string",
                description="Entity/ADGE short name. Leave empty for all entities.",
                required=False,
                default="",
            ),
            NativeFunctionParameter(
                name="year",
                type="integer",
                description="Year to filter (e.g. 2026). Leave 0 for no year filter.",
                required=False,
                default=0,
            ),
            NativeFunctionParameter(
                name="month",
                type="integer",
                description="Month number (1-12). Leave 0 for no month filter.",
                required=False,
                default=0,
            ),
        ],
        dax_template="""DEFINE
    {date_filter_var}

    {entity_filter_var}

    VAR __Core =
        SUMMARIZECOLUMNS(
            'dimadge'[ShortName],
            {date_filter_ref}
            {entity_filter_ref}
            "CES", FORMAT([CES], "0.0%"),
            "Total Responses", [Total Feedback Responses Received]
        )

    VAR __Filtered =
        FILTER(
            __Core,
            [Total Responses] >= 30
        )

EVALUATE
    __Filtered
ORDER BY
    [CES] DESC""",
        notes="Native function: CES by entity.",
        used_tables=["factadfeedback", "dimadge", "dimdate"],
        used_columns=["dimadge[ShortName]"],
        used_measures=["CES", "Total Feedback Responses Received"],
    ),

    # ----------------------------------------------------------
    # FEEDBACK: Root Cause & Topic Impact Analysis
    # ----------------------------------------------------------
    NativeFunction(
        name="root_cause_topic_impact",
        domain="feedback",
        description=(
            "Root cause & topic impact analysis for CSAT decline: explains WHY "
            "CSAT dropped by showing the services and topics driving the decline "
            "between two periods. Use for any question asking why CSAT decreased, "
            "what caused a CSAT drop, or root cause analysis of CSAT."
        ),
        examples=[
            "What topics are driving the CSAT drop in March vs February 2026?",
            "Root cause analysis for CSAT decline",
            "Why did CSAT drop? Show me the topics",
            "Which services had the biggest CSAT drop and what topics caused it?",
            "Show topic impact analysis for March 2026 vs February 2026",
            "Why did the CSAT decline for Department of Energy?",
            "What caused the CSAT drop for DOH between January and February 2026?",
            "Why is CSAT lower this month compared to last month?",
            "What are the root causes of CSAT decline?",
            "Which topics are causing the CSAT decrease?",
        ],
        parameters=[
            NativeFunctionParameter(
                name="curr_year",
                type="integer",
                description="Current (comparison) period year, e.g. 2026",
                required=True,
            ),
            NativeFunctionParameter(
                name="curr_month",
                type="integer",
                description="Current (comparison) period month (1-12)",
                required=True,
            ),
            NativeFunctionParameter(
                name="prev_year",
                type="integer",
                description="Previous (baseline) period year, e.g. 2026",
                required=True,
            ),
            NativeFunctionParameter(
                name="prev_month",
                type="integer",
                description="Previous (baseline) period month (1-12)",
                required=True,
            ),
            NativeFunctionParameter(
                name="table_name",
                type="string",
                description="Fact table name containing the topic column (e.g. factadfeedback)",
                required=False,
                default="factadfeedback",
            ),
            NativeFunctionParameter(
                name="column_name",
                type="string",
                description="Topic/category column name (e.g. FeedbackTopic)",
                required=False,
                default="FeedbackTopic",
            ),
            NativeFunctionParameter(
                name="top_services",
                type="integer",
                description="Number of top services to return",
                required=False,
                default=5,
            ),
            NativeFunctionParameter(
                name="top_topics",
                type="integer",
                description="Number of top topics per service to return",
                required=False,
                default=3,
            ),
        ],
        dax_template="""DEFINE
    -- 1. Period Definitions
    VAR __CurrPeriod = FILTER(ALL('DimDate'), 'DimDate'[Year] = {curr_year} && 'DimDate'[Month] = {curr_month})
    VAR __PrevPeriod = FILTER(ALL('DimDate'), 'DimDate'[Year] = {prev_year} && 'DimDate'[Month] = {prev_month})

    -- 2. Lineage Filter (Crossjoin to avoid multi-table ALL errors)
    VAR __DimFilter =
        FILTER(
            CROSSJOIN(ALL('DimService'[ServiceNameEn]), ALL('{table_name}'[{column_name}])),
            NOT('DimService'[ServiceNameEn] IN {{{{ BLANK() }}}}) &&
            NOT('{table_name}'[{column_name}] IN {{{{ BLANK() }}}})
        )

    -- 3. Top {top_services} Services by Service-Level Drop Impact
    VAR __Svc_P1 = SUMMARIZECOLUMNS('DimService'[ServiceNameEn], __CurrPeriod, __DimFilter, "C_CSAT", [Happy Feedback Percentage], "C_Vol", [Total Feedback Responses Received])
    VAR __Svc_P2 = SUMMARIZECOLUMNS('DimService'[ServiceNameEn], __PrevPeriod, __DimFilter, "P_CSAT", [Happy Feedback Percentage])

    VAR __TopServices =
        TOPN({top_services},
            FILTER(NATURALINNERJOIN(__Svc_P1, __Svc_P2), [P_CSAT] > [C_CSAT]),
            [C_Vol] * ([P_CSAT] - [C_CSAT]), DESC
        )

    -- 4. For each service, find Top {top_topics} Topics (Topic-Level Drop Impact)
    VAR __Result =
        GENERATE(
            __TopServices,
            VAR __CurrentSvc = 'DimService'[ServiceNameEn]

            VAR __Topic_P1 = CALCULATETABLE(SUMMARIZECOLUMNS('{table_name}'[{column_name}], __CurrPeriod, __DimFilter, "TC_CSAT", [Happy Feedback Percentage], "TC_Vol", [Total Feedback Responses Received]), 'DimService'[ServiceNameEn] = __CurrentSvc)
            VAR __Topic_P2 = CALCULATETABLE(SUMMARIZECOLUMNS('{table_name}'[{column_name}], __PrevPeriod, __DimFilter, "TP_CSAT", [Happy Feedback Percentage]), 'DimService'[ServiceNameEn] = __CurrentSvc)

            VAR __TopicImpactTable =
                ADDCOLUMNS(
                    FILTER(NATURALINNERJOIN(__Topic_P1, __Topic_P2), [TP_CSAT] > [TC_CSAT]),
                    "TopicDropImpact", [TC_Vol] * ([TP_CSAT] - [TC_CSAT])
                )

            RETURN TOPN({top_topics}, __TopicImpactTable, [TopicDropImpact], DESC)
        )

EVALUATE
    __Result
ORDER BY
    [C_Vol] * ([P_CSAT] - [C_CSAT]) DESC,
    [TopicDropImpact] DESC""",
        notes="Native function: Root cause analysis — top services with biggest CSAT drop and contributing topics.",
        used_tables=["factadfeedback", "DimService", "DimDate"],
        used_columns=["DimService[ServiceNameEn]", "factadfeedback[FeedbackTopic]", "DimDate[Year]", "DimDate[Month]"],
        used_measures=["Happy Feedback Percentage", "Total Feedback Responses Received"],
    ),

]
