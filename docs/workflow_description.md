# NLtoDAX — Workflow Description

## Overview

The system translates natural-language questions into DAX queries, executes them against Power BI, and returns formatted answers with optional charts. An **LLM Query Planner** decomposes questions into domain-specific steps, each processed by a deterministic **Analyst Workflow**.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  BROWSER  (localhost:8000)                                              │
│  User types question → frontend sends POST /query + Bearer token        │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  app.py  (FastAPI)                                                      │
│                                                                         │
│  1. Validate Bearer token (MSAL)                                        │
│  2. OBO exchange → Power BI access token                                │
│  3. Call run_pipeline_sync(shared, workflow, query, token)               │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  _run_pipeline_async  (agent_workflow.py)                                │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  STEP 1 — LLM QUERY PLANNER                          [LLM call]  │  │
│  │                                                                   │  │
│  │  Input:  user question + DOMAIN_REGISTRY descriptions             │  │
│  │  Output: ExecutionPlan { steps[ {id, domain, query, depends_on} ] │  │
│  │                                                                   │  │
│  │  Example:                                                         │  │
│  │    "total transactions and CSAT for ADAFSA"                       │  │
│  │     → Step 1: domain=transactions, query="total transactions …"   │  │
│  │     → Step 2: domain=feedback,     query="CSAT score …"           │  │
│  └───────────────────────────┬───────────────────────────────────────┘  │
│                              │                                          │
│                              ▼                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  STEP 2 — SEQUENTIAL PLAN EXECUTION                               │  │
│  │                                                                   │  │
│  │  For each plan step:                                              │  │
│  │                                                                   │  │
│  │    ● If depends_on → inject prior step's results as context       │  │
│  │    ● Load domain schema file (from DOMAIN_REGISTRY)               │  │
│  │    ● Run Analyst Workflow (see below)                             │  │
│  │    ● Collect {columns, data, dax_query, timings}                  │  │
│  │    ● On auth error → stop immediately                             │  │
│  │    ● On failure with dependents → stop                            │  │
│  └───────────────────────────┬───────────────────────────────────────┘  │
│                              │                                          │
│                              ▼                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  STEP 3 — FORMAT + CHART                                          │  │
│  │                                                                   │  │
│  │  Single-domain, ≤50 rows  → programmatic formatting (instant)     │  │
│  │  Single-domain, >50 rows  → LLM formatting              [LLM]    │  │
│  │  Cross-domain (2+ results)→ LLM cross-domain merge      [LLM]    │  │
│  │                                                                   │  │
│  │  + ChartVisualizer → Chart.js config (if chartable data)          │  │
│  │  + PipelineTiming  → markdown timing block appended to answer     │  │
│  └───────────────────────────┬───────────────────────────────────────┘  │
│                              │                                          │
│                              ▼                                          │
│  Return: {formatted_answer, chart_config, dax_query, timing, …}        │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  app.py → QueryResponse JSON → Browser renders answer + chart           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Analyst Workflow (per plan step)

Each plan step runs one instance of the analyst workflow — an `agent_framework` graph:

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  ① GenerateDAXExecutor                          [LLM call]   │
│     • Receives: user query + domain schema                   │
│     • Sends schema + question to LLM (streaming)             │
│     • Outputs: DAX query string                              │
│                     │                                        │
│                     ▼                                        │
│  ② ExecuteDAXExecutor                                        │
│     • Runs DAX against Power BI REST API                     │
│     • Success? ──────────────→ ④ Output (END)                │
│     • Failure?  ──┐                                          │
│                   ▼                                          │
│  ③ ValidateDAXExecutor                          [LLM call]   │
│     • Sends failed DAX + error message to LLM               │
│     • LLM returns corrected DAX                              │
│     • Loops back to ② (up to 2 retries)                      │
│                                                              │
│  ④ WorkflowOutputExecutor                                    │
│     • Emits final result: columns, data, row_count, timings  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## LLM Calls Per Query Type

| Scenario | Planner | DAX Gen | DAX Validate | Formatter | Total |
|---|:---:|:---:|:---:|:---:|:---:|
| Single-domain, ≤50 rows | 1 | 1 | — | — | **2** |
| Single-domain, >50 rows | 1 | 1 | — | 1 | **3** |
| Cross-domain (2 domains) | 1 | 2 | — | 1 | **4** |
| Any + DAX retry | — | — | +1/retry | — | +1 |

---

## Key Files

| File | Role |
|---|---|
| `app.py` | FastAPI server, auth, `/query` endpoint |
| `backend/agent_workflow.py` | Pipeline orchestrator (`_run_pipeline_async`) |
| `backend/tools/query_planner.py` | LLM Query Planner → `ExecutionPlan` |
| `backend/tools/generate_dax.py` | Streaming DAX generation (LLM) |
| `backend/tools/validate_dax.py` | DAX error correction (LLM) |
| `backend/tools/execute_dax.py` | Power BI REST API execution |
| `backend/tools/format_dax_results.py` | Programmatic + LLM result formatting |
| `backend/tools/chart_visualizer.py` | Chart.js config generation |
| `backend/tools/auth.py` | Shared `AsyncOpenAI` client, token management |
| `backend/prompts/domain_registry.py` | Domain definitions (name → schema file) |
| `backend/prompts/query_planner_prompt.py` | Planner system prompt |
| `backend/prompts/dax_generator_prompt_*.py` | Per-domain DAX generation prompts |
| `backend/utils/timing.py` | `PipelineTiming` — structured timing tracker |

---

## Adding a New Domain

1. **Schema** — Add the schema `.txt` file under `cache/schema/`
2. **`schema_extraction/domain_configs.py`** — Add a `DOMAIN_CONFIGS` entry for schema extraction
3. **`backend/prompts/domain_registry.py`** — Add a `DOMAIN_REGISTRY` entry (description + schema path)
4. **`backend/prompts/`** — Create `dax_generator_prompt_<domain>.py` and `dax_validator_prompt_<domain>.py`

No code changes needed in the pipeline — the planner automatically discovers new domains from the registry.
