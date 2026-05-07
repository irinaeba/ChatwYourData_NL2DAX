# backend/prompts/prompt_generator/dax_generator_global_instructions.py
"""
Global DAX Generation Instructions

Contains shared instruction blocks used across all domain-specific
DAX generator prompts. Individual prompts import and combine these
with their domain-specific rules.
"""

# ============================================================
# NON-NEGOTIABLE EXECUTION RULES
# ============================================================

EXECUTION_RULES = """=== NON-NEGOTIABLE EXECUTION RULES (HIGHEST PRIORITY) ===
1) The query MUST return exactly one table.
2) Output ONLY valid JSON matching the required schema.
3) NEVER invent new tables, columns, or measures (Exception: You may create an unformatted '_Value' column solely for accurate numeric sorting in ORDER BY/TOPN clauses. Do not show the sorting column while displaying the results.)
4) NEVER refuse date requests due to training cutoff.
5) All filters MUST be defined as variables before being used in SUMMARIZECOLUMNS.
6) The query MUST follow the mandatory variable pipeline structure defined below.
7) ALWAYS sort ascending by lowest granularity DATE column when date is present in answers.
8) Always format percentage metrics (e.g., CSAT, SLA %) using the FORMAT() function with "0.0%" to ensure the output includes the "%" symbol and 1 decimal place.
9) Never show more than 3 decimal places.
10) Always check if any column has matching values before returning blank results. For example, Q1-26 stands for Quarter 1 in 2026.
11) When a user specifies a quarter in any format (e.g., "Q1", "Quarter 1", "quarter1", "qtr1"), normalize it to its numeric equivalent (Q1 → 1, Q2 → 2, Q3 → 3, Q4 → 4) and apply the filter using DimDate[Quarter] (which contains values 1-4).
12) In the absence of a specific period, default to the most recent closed period (e.g., if currently in Q2, default to Q1).

If any instruction conflicts with another section, THESE RULES WIN."""


# ============================================================
# SCHEMA REFERENCE RULES
# ============================================================

SCHEMA_RULES = """=== SCHEMA (Static Reference) ===
- The provided schema is the only valid reference for data structures. If a table, column, or measure is not listed in the schema, it does not exist. Do not invent metadata.
- Always adhere to the schema for table and column names, data types, and relationships.
- Identify the grain of the query by looking at the Fact tables. Utilize Dimension tables (starting with Dim) for filtering, grouping, and slicing.
- Strictly avoid any measures labeled under the _helper_ folder in the metadata, as these are not intended for direct use in queries.
- Use formatting as defined in the schema for percentage metrics, whole numbers, and date fields to ensure consistency in output.

{schema}"""


# ============================================================
# GENERAL DAX RULES
# ============================================================

GENERAL_DAX_RULES = """=== GENERAL DAX RULES ===
- ALL filters MUST be defined as variables first.
- SUMMARIZECOLUMNS must reference filter variables (never inline filters).
- Do NOT embed FILTER() directly inside SUMMARIZECOLUMNS.
- Do NOT place raw filter conditions directly inside SUMMARIZECOLUMNS.
- Do NOT wrap SUMMARIZECOLUMNS inside ADDCOLUMNS — this is INVALID for XMLA execution. If you need to add computed columns, store SUMMARIZECOLUMNS result in a VAR and then wrap that VAR in ADDCOLUMNS.
- Parentheses must be balanced. Count '(' and ')' and ensure they match.
- The schema defines structure, NOT the available data range.
- The database contains live data.
- ALWAYS generate queries for requested years (2024–2027+).
- NEVER refuse due to year/date concerns.
- Use DimDate for date filtering.
- ALWAYS sort ascending by DATE when date is present in answers.
- Only use tables, columns, measures present in metadata.
- Prefer existing measures when available.
- Ignore measures under the _helper_ folder.
- Respect previous conversation context if follow-up.
- Never display the categorical values as keys, try to look for a valid english or arabic name column from relevant dim table.
- When ranking (TOPN) or sorting (ORDER BY) percentage metrics, you MUST generate a raw numeric column (e.g., 'Metric_Value') to use for sorting, alongside the FORMAT() string metric for display.
- Do not show the sorting column if there is another similar column with the right formatting.
- Rename the columns appropriately in a user-friendly way before displaying."""


# ============================================================
# MANDATORY QUERY STRUCTURE
# ============================================================

MANDATORY_QUERY_STRUCTURE = """=== MANDATORY QUERY STRUCTURE ===

1. GENERAL QUERY STRUCTURE

DEFINE
    VAR __DS0FilterTable = 
        FILTER(
            'TABLENAME',
            'TABLENAME'[ColumnName] = ColumnVALUE
        )

    VAR __Core =
        SUMMARIZECOLUMNS(
            GROUPING_COLUMNS_IF_ANY,
            __DS0FilterTable,
            "Metric1", [Measure1],
            "Metric2", [Measure2]
        )

    VAR __Result =
        __Core

EVALUATE
    __Result

Rules:
- ALL filters MUST be defined as variables first.
- SUMMARIZECOLUMNS must reference filter variables (never inline filters).
- Do NOT embed FILTER() directly inside SUMMARIZECOLUMNS.

If ranking is required:
    VAR __Result = TOPN(N, __Core, [Metric], DESC)

If sorting is required:
    ORDER BY [ColumnName] ASC

2. COMPARISON QUERY STRUCTURE

DEFINE
    -- Period 1 Aggregate
    VAR __Period1 =
        SUMMARIZECOLUMNS(
            'Dimension'[Attribute],
            FILTER('DimDate', 'DimDate'[Year] = YYYY && 'DimDate'[Month] = M),
            "Metric_P1", [Measure]
        )

    -- Period 2 Aggregate
    VAR __Period2 =
        SUMMARIZECOLUMNS(
            'Dimension'[Attribute],
            FILTER('DimDate', 'DimDate'[Year] = YYYY && 'DimDate'[Month] = M),
            "Metric_P2", [Measure]
        )

    VAR __Joined = NATURALINNERJOIN(__Period1, __Period2)

    VAR __ValidRows =
        FILTER(__Joined, NOT ISBLANK([Metric_P1]) && NOT ISBLANK([Metric_P2]))

    VAR __WithDelta =
        ADDCOLUMNS(
            __ValidRows,
            "Abs Delta", [Metric_P2] - [Metric_P1],
            "Percentage Change", DIVIDE([Metric_P2] - [Metric_P1], [Metric_P1])
        )

EVALUATE
    __WithDelta

3. TREND / TIME-PERIOD QUERY STRUCTURE (Last X Months)

DEFINE
    VAR __ReferenceDate = TODAY()
    VAR __StartRange = EOMONTH(__ReferenceDate, -4) + 1
    VAR __EndRange = EOMONTH(__ReferenceDate, -1)

    VAR __DateFilter =
        FILTER(
            ALL('DimDate'),
            'DimDate'[Date] >= __StartRange &&
            'DimDate'[Date] <= __EndRange
        )

    VAR __Core =
        SUMMARIZECOLUMNS(
            'DimDate'[Year],
            'DimDate'[Month],
            'DimDate'[MonthName],
            __DateFilter,
            "Metric", [Measure]
        )

EVALUATE
    __Core
ORDER BY
    'DimDate'[Year] ASC,
    'DimDate'[Month] ASC"""


# ============================================================
# DATE HANDLING RULES
# ============================================================

DATE_HANDLING_RULES = """=== DATE HANDLING ===
- The schema defines structure, NOT the available data range.
- The database contains live data.
- ALWAYS generate queries for requested years (2024–2027+).
- NEVER refuse due to year/date concerns.
- Use DimDate for date filtering.
- ALWAYS sort ascending by lowest granularity DATE column when date is present.

Examples:
    January 2026: DimDate[Year] = 2026 && DimDate[Month] = 1
    Q1 2026: DimDate[Year] = 2026 && DimDate[Quarter] = 1

All date filters MUST be wrapped in a filter variable."""


# ============================================================
# RESULT SHAPE RULES
# ============================================================

RESULT_SHAPE_RULES = """=== RESULT SHAPE RULES ===

If single scalar result:
    EVALUATE ROW("Metric Name", [Measure])

If grouped result:
    Use SUMMARIZECOLUMNS with explicit grouping columns.

Do NOT use: EMPTYTABLE(), DATATABLE(), or dummy placeholder tables.

Apply TOPN(10, ...) ONLY when result may contain multiple rows.
Do NOT wrap scalar results in TOPN."""


# ============================================================
# OUTPUT FORMAT
# ============================================================

OUTPUT_FORMAT = """=== OUTPUT FORMAT (JSON) ===

{{
    "query": "EVALUATE ...",
    "notes": "brief explanation of the query",
    "used": {{
        "tables": ["table names used"],
        "columns": ["column names used"],
        "measures": ["measure names used"]
    }}
}}

If the query cannot be generated:

{{
    "error": "reason why query cannot be generated"
}}"""


# ============================================================
# USER REQUEST SECTION
# ============================================================

USER_REQUEST_SECTION = """=== USER REQUEST (Dynamic) ===
Generate a DAX query for the following user question:
{user_query}

Return ONLY the JSON. No explanations outside JSON."""
