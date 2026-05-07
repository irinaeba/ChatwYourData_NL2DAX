# NLtoDAX — Workflow Description

## Overview

The system translates natural-language questions into DAX queries, executes them against Power BI, and returns formatted answers with optional charts. An **LLM Query Planner** decomposes questions into domain-specific steps, each processed by a deterministic **Analyst Workflow** built on the `agent-framework` library.

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
│  2. OBO exchange → Power BI access token (cached)                       │
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
│  │  Input:  user question + DOMAIN_REGISTRY + conversation history   │  │
│  │  Output: ExecutionPlan { steps[ {id, domain, query, depends_on} ] │  │
│  │          OR clarification_needed with suggestions                 │  │
│  │                                                                   │  │
│  │  Example:                                                         │  │
│  │    "work orders and complaints for Q1 2025"                       │  │
│  │     → Step 1: domain=work_orders, query="work orders for Q1…"    │  │
│  │     → Step 2: domain=citizen_complaints, query="complaints…"      │  │
│  └───────────────────────────┬───────────────────────────────────────┘  │
│                              │                                          │
│                              ▼                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  STEP 2 — SEQUENTIAL PLAN EXECUTION                               │  │
│  │                                                                   │  │
│  │  For each plan step:                                              │  │
│  │                                                                   │  │
│  │    ● If depends_on → inject prior step's results as context       │  │
│  │    ● Try native function fast path (parameterized DAX template)   │  │
│  │      → If matched & executed: skip LLM generation entirely        │  │
│  │    ● Else: Load domain schema, run Analyst Workflow (see below)   │  │
│  │    ● Collect {columns, data, dax_query, timings}                  │  │
│  │    ● On auth error → stop immediately                             │  │
│  │    ● On failure with dependents → stop                            │  │
│  └───────────────────────────┬───────────────────────────────────────┘  │
│                              │                                          │
│                              ▼                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  STEP 3 — FORMAT + CHART                                          │  │
│  │                                                                   │  │
│  │  Single-domain, ≤50 rows  → programmatic formatting (no LLM)     │  │
│  │  Single-domain, >50 rows  → LLM formatting              [LLM]    │  │
│  │  Cross-domain (2+ results)→ LLM cross-domain merge      [LLM]    │  │
│  │                                                                   │  │
│  │  + ChartVisualizer → Chart.js config (line or bar chart)          │  │
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

Each plan step runs one instance of the analyst workflow — an `agent-framework` DAG built with `WorkflowBuilder`:

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  ① GenerateDAXExecutor                          [LLM call]   │
│     • Receives: user query + domain schema + history         │
│     • Streams DAX from LLM (captures TTFT/TTLT metrics)      │
│     • Outputs: DAX query string + generation notes           │
│                     │                                        │
│                     ▼                                        │
│  ② ExecuteDAXExecutor                                        │
│     • Runs DAX against Power BI REST API                     │
│     • Success? ──────────────→ ④ Output (END)                │
│     • Failure? ───┐                                          │
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

**Workflow graph edges:**
- `GenerateDAX` → `ExecuteDAX` (always)
- `ValidateDAX` → `ExecuteDAX` (always)
- `ExecuteDAX` → `ValidateDAX` (conditional: phase == RETRY_VALIDATE)
- `ExecuteDAX` → `Output` (default: success or retries exhausted)

**State management:** Each workflow run uses a `DAXWorkflowState` dataclass that tracks phase (`NORMAL` / `RETRY_VALIDATE` / `FORMAT` / `FAILED`), retry count, error history, conversation turns, and chart metadata.

---

## Native Functions (Fast Path)

Before invoking the LLM analyst workflow, the pipeline attempts to match the user's query against **parameterized DAX templates** — prewritten, validated queries for common patterns.

```
User Query → LLM Matcher → Match?
  ├─ YES → Render template with extracted params → Execute → Done
  └─ NO  → Fall through to standard LLM DAX generation
```

**How it works:**
1. `matcher.py` sends the query + function catalog (names, descriptions, examples) to the LLM
2. LLM decides if any template matches and extracts parameters (e.g., date ranges, entities)
3. `registry.py` renders the DAX template with parameters via `NativeFunction.render(params)`
4. The rendered DAX is executed directly — no generation or validation step needed

**Benefits:** Faster execution (~1s vs ~5-8s), deterministic DAX for known query patterns, no retry loops.

**Config:** Templates defined in `backend/native_functions/registry.py` as `NATIVE_FUNCTIONS` list.

---

## LLM Calls Per Query Type

| Scenario | Planner | Native Match | DAX Gen | DAX Validate | Formatter | Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Native function hit, ≤50 rows | 1 | 1 | — | — | — | **2** |
| Single-domain, ≤50 rows | 1 | 1 | 1 | — | — | **3** |
| Single-domain, >50 rows | 1 | 1 | 1 | — | 1 | **4** |
| Cross-domain (2 domains) | 1 | 2 | 2 | — | 1 | **6** |
| Any + DAX retry | — | — | — | +1/retry | — | +1 |

> Native function matching always runs (1 LLM call) but is fast (~1s). If it matches, generation is skipped.

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
| `backend/tools/chart_visualizer.py` | Chart.js config generation (line / horizontal bar) |
| `backend/tools/auth.py` | Shared `AsyncOpenAI` client, token management |
| `backend/native_functions/registry.py` | Native function catalog (parameterized DAX templates) |
| `backend/native_functions/matcher.py` | LLM-based native function matching |
| `backend/executors/generate_dax_executor.py` | Executor node: DAX generation |
| `backend/executors/validate_dax_executor.py` | Executor node: DAX validation/correction |
| `backend/executors/execute_dax_executor.py` | Executor node: DAX execution + retry logic |
| `backend/executors/workflow_state.py` | `DAXWorkflowState` — shared state dataclass |
| `schema_extraction/domain_configs.py` | Domain definitions + `DOMAIN_REGISTRY` (name → schema file) |
| `backend/prompts/query_planner_prompt.py` | Planner system prompt |
| `backend/prompts/prompt_generator/` | Per-domain DAX generation prompts |
| `backend/prompts/prompt_validator/` | Per-domain DAX validation prompts |
| `backend/prompts/answer_formatter_prompt.py` | LLM answer formatting prompt |
| `backend/utils/timing.py` | `PipelineTiming` — structured timing tracker |

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Health check → `{status, initialized, timestamp}` |
| `GET` | `/status` | Detailed status → workflow type, schema cache date |
| `GET` | `/auth/config` | MSAL.js configuration for frontend (no secrets) |
| `POST` | `/auth/initialize` | OBO exchange + Power BI warm-up query |
| `GET` | `/evaluations/questions` | Pre-defined questions from CSV for UI sidebar |
| `POST` | `/query` | Main query endpoint (requires Bearer token) |
| `GET` | `/` | Serves `frontend/index.html` |

---

## Adding a New Domain

1. **Schema extraction config** — Add a `DOMAIN_CONFIGS` entry in `schema_extraction/domain_configs.py` (this auto-generates the `DOMAIN_REGISTRY` entry)
2. **Run schema extraction** — `python schema_extraction/automated_schema_extract.py --save-json`
3. **DAX generation prompt** — Create `backend/prompts/prompt_generator/dax_generator_prompt_<domain>.py`
4. **DAX validation prompt** — Create `backend/prompts/prompt_validator/dax_validator_prompt_<domain>.py`

No code changes needed in the pipeline — the planner automatically discovers new domains from the registry.
