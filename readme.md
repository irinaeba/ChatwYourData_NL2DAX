# NLtoDAX - Natural Language to DAX Query Generator

A chat-based application that converts natural language questions into DAX queries and executes them against Power BI semantic models.

![Chat with your Data](docs/chatwdata.png)

---

## [Solution Setup Guide →](docs/SOLUTION_SETUP.md)

Follow the complete setup instructions for app registration, Azure OpenAI, Power BI, local development, and Docker.

[Open the setup guide →](docs/SOLUTION_SETUP.md)

---

## �📁 Project Structure

```
NLtoDAX/
├── app.py                 # Main FastAPI application
├── Dockerfile             # Multi-stage Docker build
├── docker-compose.yml     # Docker Compose orchestration
├── env.template           # Environment variables template
├── requirements.txt
├── backend/
│   ├── executors/         # Workflow executors
│   ├── prompts/
│   │   ├── prompt_generator/  # DAX generation prompts (per domain)
│   │   └── prompt_validator/  # DAX validation prompts (per domain)
│   ├── tools/             # Core tools (auth, DAX execution, chart viz)
│   └── evaluations/       # Evaluation scripts and ground truth
├── frontend/              # Chat UI (HTML/CSS/JS)
├── cache/
│   └── schema/            # Cached semantic model schemas
├── schema_extraction/     # Schema extraction utilities
└── lib/                   # ADOMD.NET DLL (local dev only)
```

---

## �️ Environment Setup (Quick Start)

Follow these steps to set up the application from scratch:

### 1. Create `.env` file

Copy the template and fill in your values:

```bash
cp env.template .env
```

Required variables:

```env
TENANT_ID=<your-azure-ad-tenant-id>

# Power BI App Registration
CLIENT_ID_POWERBI=<power-bi-app-client-id>
CLIENT_SECRET_POWERBI=<power-bi-app-client-secret>
API_SCOPE=api://<power-bi-app-client-id>/access_as_user

# Schema Extraction Service Principal (can be same as Power BI app)
CLIENT_ID_POWERBI_SCHEMA_EXTRACTION=<schema-extraction-client-id>
CLIENT_SECRET_POWERBI_SCHEMA_EXTRACTION=<schema-extraction-client-secret>

# Azure OpenAI
CLIENT_ID_OPENAI=<openai-app-client-id>
CLIENT_SECRET_OPENAI=<openai-app-client-secret>
AZURE_OPENAI_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com
AZURE_OPENAI_DEPLOYMENT=<your-deployment-name>
AZURE_OPENAI_API_VERSION=2025-04-01-preview

# LLM Provider Toggle ("azure" or "compass")
LLM_PROVIDER=azure

# Power BI Workspace / Semantic Model
WORKSPACE_NAME=<your-workspace-name>
WORKSPACE_ID=<your-workspace-guid>
DATABASE_NAME=<your-semantic-model-name>
DATASET_ID=<your-dataset-guid>
```

### 2. Configure Domain Schema Extraction

Edit `schema_extraction/domain_configs.py` to match your semantic model's domain structure.

This file defines how the full schema is split into domain-specific prompts. Each domain config specifies:
- Which fact table(s) belong to the domain
- Which dimension tables to include
- Output file prefix and description

> **Note:** This is already configured for the demo data (`SM_SyntheticData_DEMO`). Update if connecting to your own model.

### 3. Run Schema Extraction

Extract the schema from your Power BI semantic model via the XMLA endpoint:

```bash
python schema_extraction/automated_schema_extract.py --save-json
```

This will:
- Connect to Power BI via XMLA (uses `CLIENT_ID_POWERBI_SCHEMA_EXTRACTION` credentials)
- Extract all tables, columns, measures, and relationships
- Save the full schema as JSON and formatted text
- Split into domain-specific schemas under `cache/schema/`

Output files:
```
cache/schema/
├── schema_pack_<date>.json        # Full schema (JSON)
├── schema_pack_<date>.txt         # Full schema (formatted text)
├── schema_work_orders.txt         # Domain: work orders
├── schema_complaints.txt          # Domain: citizen complaints
├── schema_maintenance_costs.txt   # Domain: maintenance costs
└── schema_asset_downtime.txt      # Domain: asset downtime
```

> **Prerequisites:** Requires Windows with ADOMD.NET DLL (`lib/net45/` or `lib/netcore/`), `pyadomd`, and XMLA read enabled on the workspace.

### 4. Build and Run with Docker

```bash
# Build the container
docker compose up --build -d

# Verify it's healthy (~30s for first startup)
docker ps                          # STATUS should show "(healthy)"
curl http://localhost:8000/health   # Returns {"status":"healthy",...}

# Open the UI
# Browse to http://localhost:8000
```

The Docker container:
- Uses Python 3.12-slim with a multi-stage build
- Loads `.env` at runtime (never baked into the image)
- Exposes port **8000**
- Includes a healthcheck that pings `/health` every 30s
- Copies `backend/`, `frontend/`, `cache/`, and `schema_extraction/` into the image

Common commands:
```bash
docker logs nltodax-app -f           # Follow logs
docker compose down                  # Stop
docker compose up -d --build         # Rebuild after code changes
docker compose restart               # Restart without rebuild
```

---

## �🔧 Troubleshooting

### Authentication Errors
- Ensure all app registrations have correct permissions
- Verify client secrets haven't expired
- Check that the OpenAI app has the Cognitive Services role assigned

### XMLA Connection Issues
- Verify XMLA read/write is enabled on the workspace
- Ensure the user has appropriate permissions on the semantic model
- Check the ADOMD_DLL path is correct

---

## 📄 License

This project is for demonstration purposes.
