# NLtoDAX Production Deployment Guide

This guide provides a comprehensive plan for deploying the NLtoDAX solution as a **Citadel Agent Spoke (CAS)** on Azure Container Apps. The deployment is designed to operate standalone in Phase 1 and integrate with a centrally managed **Citadel Governance Hub (CGH)** via VNet peering when that becomes available.

The architecture follows the [Foundry Citadel Platform](https://github.com/Azure-Samples/foundry-citadel-platform) patterns — Microsoft's layered approach to AI governance, security, and observability — and aligns with the Azure Well-Architected Framework for AI workloads.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Citadel Alignment & CAS Design Decisions](#citadel-alignment--cas-design-decisions)
3. [Production Services Inventory](#production-services-inventory)
4. [Prerequisites](#prerequisites)
5. [Phase 1: Project Structure Setup](#phase-1-project-structure-setup)
6. [Phase 2: Containerization](#phase-2-containerization)
7. [Phase 3: Infrastructure as Code (Bicep)](#phase-3-infrastructure-as-code-bicep)
8. [Phase 4: App Registrations (Programmatic)](#phase-4-app-registrations-programmatic)
9. [Phase 5: Secrets & Configuration Management](#phase-5-secrets--configuration-management)
10. [Phase 6: CI/CD Pipeline (GitHub Actions)](#phase-6-cicd-pipeline-github-actions)
11. [Phase 7: One-Click Deployment Experience](#phase-7-one-click-deployment-experience)
12. [Phase 8: Frontend-Backend Communication](#phase-8-frontend-backend-communication)
13. [Phase 9: Monitoring & Observability](#phase-9-monitoring--observability)
14. [Phase 10: Production State Management](#phase-10-production-state-management)
15. [Implementation Roadmap](#implementation-roadmap)
16. [Future: CGH Integration](#future-cgh-integration)

---

## Architecture Overview

### Citadel Agent Spoke (CAS) — Standalone Phase 1

The NLtoDAX solution deploys as a **Citadel Agent Spoke (CAS)** — a self-contained, VNet-integrated AI agent workload. It operates independently, calling Azure OpenAI directly, and is designed to connect to a central **Citadel Governance Hub (CGH)** via VNet peering at a later stage.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│   CITADEL AGENT SPOKE (CAS) — NLtoDAX  (Standalone Phase 1)                 │
│   Resource Group: rg-nltodax-prod                                            │
│                                                                              │
│  ┌──────── Container Apps Environment (VNet-integrated) ──────────────────┐  │
│  │  ┌──────────────┐           ┌──────────────────────────────────────┐   │  │
│  │  │ Frontend CA  │──HTTPS──▶ │ Backend CA (FastAPI)                 │   │  │
│  │  │ (nginx+SPA)  │           │  ├─ IntentExtractor                  │   │  │
│  │  │ MSAL.js auth │           │  ├─ DAXGenerator ──▶ Azure OpenAI    │   │  │
│  │  │              │           │  ├─ DAXValidator ──▶ Azure OpenAI    │   │  │
│  │  └──────────────┘           │  ├─ DAXExecutor  ──▶ Power BI XMLA   │   │  │
│  │                             │  ├─ ResultFormatter                  │   │  │
│  │                             │  └─ ChartVisualizer                  │   │  │
│  │                             └──────────────────────────────────────┘   │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌───────────┐      │
│  │ Key Vault  │ │ ACR      │ │ Redis    │ │ Blob       │ │ App       │      │
│  │ (secrets)  │ │ (images) │ │ Cache    │ │ Storage    │ │ Insights  │      │
│  └────────────┘ └──────────┘ └──────────┘ └────────────┘ └───────────┘      │
│                                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────────────┐    │
│  │ Log Analytics│  │ Managed      │  │ VNet: 10.1.0.0/16              │    │
│  │ Workspace    │  │ Identity     │  │  ├─ snet-apps  (Container Apps)│    │
│  └──────────────┘  └──────────────┘  │  ├─ snet-data  (Redis, Storage)│    │
│                                      │  ├─ snet-infra (KV, ACR PEs)  │    │
│                                      │  └─ (reserved for future)      │    │
│                                      └─────────────────────────────────┘    │
│                                                                              │
│  Azure OpenAI called directly via configurable LLM_ENDPOINT env var          │
│  (re-routable to CGH APIM AI Gateway later without code changes)             │
└──────────────────────────────────────────────────────────────────────────────┘
                                 │
              ┌──────────────────┴──────────────────────────┐
              │              External Services               │
              │  ┌─────────────┐ ┌──────────┐ ┌───────────┐ │
              │  │ Azure OpenAI│ │ Power BI │ │ Entra ID  │ │
              │  │ (gpt-5-mini)│ │ XMLA     │ │ (Auth)    │ │
              │  └─────────────┘ └──────────┘ └───────────┘ │
              └─────────────────────────────────────────────┘
```

### Future: With CGH Connected (Phase 2)

```
┌──────────────────────────────────────────────────────────────────────────┐
│      CITADEL GOVERNANCE HUB (CGH) — Central (deployed separately)        │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────────────┐  │
│  │ APIM AI Gateway│  │ API Center     │  │ AI Foundry Control Plane  │  │
│  │ (routes LLM    │  │ (AI registry)  │  │ (evaluations, compliance) │  │
│  │  traffic)      │  │                │  │                            │  │
│  └───────┬────────┘  └────────────────┘  └────────────────────────────┘  │
│          │                                                               │
│  ┌───────┴────────┐  ┌────────────────┐  ┌────────────────────────────┐  │
│  │ Content Safety │  │ Cosmos DB      │  │ Log Analytics (central)   │  │
│  │ + Prompt Shield│  │ (usage/cost)   │  │                            │  │
│  └────────────────┘  └────────────────┘  └────────────────────────────┘  │
│  VNet: 10.0.0.0/16                                                       │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │ VNet Peering
┌──────────────────────────────┴───────────────────────────────────────────┐
│   CITADEL AGENT SPOKE (CAS) — NLtoDAX                                    │
│   (same as Phase 1 — LLM_ENDPOINT env var re-pointed to APIM gateway)    │
│   VNet: 10.1.0.0/16                                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Purpose | Citadel Layer |
|-----------|---------|---------------|
| **Frontend Container App** | Serves static HTML/JS/CSS via nginx, MSAL.js auth | CAS |
| **Backend Container App** | FastAPI with 5-step DAX workflow (agent) | CAS |
| **Azure Container Registry (ACR)** | Stores Docker images, vulnerability scanning | CAS |
| **Azure Key Vault** | Secrets: client secrets, connection strings | CAS |
| **Azure Cache for Redis** | Token cache, workflow state (multi-replica safe) | CAS |
| **Azure Blob Storage** | Schema cache, chat history persistence | CAS |
| **Log Analytics Workspace** | Centralized container logs | CAS |
| **Application Insights** | Distributed traces, request metrics, latency | CAS (feeds CGH later) |
| **Virtual Network** | Network isolation, private endpoints, future peering | CAS |
| **Managed Identity** | Passwordless auth to KV, ACR, Blob, Redis | CAS |
| **Container Apps Environment** | Shared compute, VNet-integrated | CAS |

---

## Citadel Alignment & CAS Design Decisions

This deployment follows the [Foundry Citadel Platform](https://github.com/Azure-Samples/foundry-citadel-platform) hub-and-spoke architecture. The CAS (Agent Spoke) is designed to operate standalone and later integrate with a CGH (Governance Hub) via VNet peering. Five critical decisions ensure this integration is a configuration exercise, not a re-architecture.

### Design Decision 1: VNet Address Space — Avoid Overlaps

The CAS uses `10.1.0.0/16`. The CGH typically uses `10.0.0.0/16`. If they overlap, VNet peering is impossible.

```
CAS (this deployment): 10.1.0.0/16
Future CGH:            10.0.0.0/16  (confirm with CGH team)

Subnets:
├── snet-apps:    10.1.1.0/24   (Container Apps Environment delegation)
├── snet-data:    10.1.2.0/24   (Redis, Blob Storage private endpoints)
├── snet-infra:   10.1.3.0/24   (Key Vault, ACR private endpoints)
└── snet-mgmt:    10.1.4.0/24   (future: jump VM, bastion, build agent)
```

> **Action**: Confirm with the team planning CGH what address range they'll use before deploying.

### Design Decision 2: Configurable LLM Endpoint

Azure OpenAI is called via a single environment variable `LLM_ENDPOINT` (mapped to `AZURE_OPENAI_ENDPOINT`). When CGH deploys its APIM AI Gateway, this variable changes to the APIM URL — **no code changes needed**.

```python
# Phase 1 (standalone): Direct to Azure OpenAI
LLM_ENDPOINT = "https://your-aoai.openai.azure.com"

# Phase 2 (CGH connected): Re-route through APIM AI Gateway
LLM_ENDPOINT = "https://apim-citadel-hub.azure-api.net/openai"
```

APIM's AI Gateway speaks the OpenAI-compatible API natively, so Semantic Kernel won't know the difference.

### Design Decision 3: Structured OpenTelemetry from Day One

Even without CGH's central observability, the CAS emits proper OpenTelemetry traces to its own Application Insights from day one. When CGH arrives, these traces are forwarded to the central Log Analytics workspace via diagnostic settings.

Replace `print(f"[TIMING]...")` statements in the workflow with OpenTelemetry spans:
- Parent span: full query lifecycle
- Child spans: IntentExtractor, DAXGenerator (with TTFT/TTLT), DAXValidator, DAXExecutor (XMLA time), ResultFormatter

### Design Decision 4: Subnet Design Matching Citadel CAS Patterns

Subnets follow the Citadel spoke blueprint for plug-and-play peering. See the VNet layout in Design Decision 1.

### Design Decision 5: AI Access Contract

Document what governed resources the agent needs. This contract is submitted to the CGH for onboarding when the hub is deployed.

```yaml
# citadel-ai-access-contract.yaml (place in repo root)
agent:
  name: NLtoDAX-Agent
  type: BYO  # Bring Your Own (custom FastAPI + Semantic Kernel)
  sponsor: <owner-email>

dependencies:
  llm:
    - service: Azure OpenAI
      model: gpt-5-mini
      usage: DAX generation, validation, intent extraction, formatting
      estimated_tokens_per_request: ~4000
  tools:
    - name: Power BI XMLA
      protocol: ADOMD.NET / REST
      scopes: [Dataset.Read.All, Workspace.Read.All]
  data:
    - type: schema_cache
      sensitivity: internal
    - type: query_results
      sensitivity: potentially_contains_PII

security:
  auth_pattern: BFF + OBO
  user_auth: Entra ID (MSAL.js)
  agent_auth: Managed Identity + Service Principal
```

### What NOT to Deploy in Phase 1

These are CGH-layer concerns that will be provided centrally:

| Do NOT Deploy | Why |
|---------------|-----|
| Your own APIM | CGH provides the centralized AI Gateway |
| Your own Content Safety | CGH applies content policies at the gateway |
| Cost attribution system | CGH APIM + Cosmos DB handles this centrally |
| AI Registry / API Center | Lives in CGH |

---

## Production Services Inventory

### Complete Service List (Phase 1 — Standalone CAS)

| # | Service | SKU / Tier | Purpose |
|---|---------|-----------|---------|
| 1 | **Resource Group** | — | `rg-nltodax-prod` |
| 2 | **Virtual Network** | — | `10.1.0.0/16` with 4 subnets |
| 3 | **Container Apps Environment** | Consumption | VNet-integrated |
| 4 | **Container App: Frontend** | 0.25 vCPU / 0.5Gi | nginx + SPA |
| 5 | **Container App: Backend** | 0.5 vCPU / 1Gi | FastAPI + workflow |
| 6 | **Azure Container Registry** | Basic | Docker images + vulnerability scanning |
| 7 | **Azure Key Vault** | Standard | Secrets: `CLIENT_SECRET_POWERBI`, `CLIENT_SECRET_OPENAI` |
| 8 | **Azure Cache for Redis** | Basic C0 | Token cache, workflow state (replaces in-memory `AppState`) |
| 9 | **Azure Blob Storage** | Standard LRS | Schema cache files, chat history |
| 10 | **Log Analytics Workspace** | PerGB2018 | Container logs, KQL queries |
| 11 | **Application Insights** | — (linked to Log Analytics) | Distributed traces, request metrics |
| 12 | **Managed Identity** | System-assigned | On backend CA — accesses KV, ACR, Blob, Redis |
| 13 | **Private DNS Zones** | — | For KV, ACR, Blob, Redis private endpoints |
| 14 | **Private Endpoints** | — | For KV, Blob, Redis (no public access) |
| 15 | **Entra ID App Registration (Backend)** | — | BFF + OBO flow |
| 16 | **Entra ID App Registration (OpenAI)** | — | Service principal for Azure OpenAI |

**Total: 16 services/resources** (plus existing external Azure OpenAI and Power BI)

### External Dependencies (Not Deployed by This Template)

| Service | Purpose | Requirement |
|---------|---------|-------------|
| **Azure OpenAI** | LLM for intent extraction, DAX generation, validation, formatting | Deployed model (e.g., `gpt-5-mini`) |
| **Power BI Service** | XMLA endpoint for executing DAX queries | Premium/PPU workspace with XMLA enabled |
| **Microsoft Entra ID** | User authentication, OBO token exchange | Tenant with app registrations |

---

## Prerequisites

Before starting, ensure you have:

- [ ] Azure subscription with Contributor access
- [ ] Azure CLI installed (`az --version`)
- [ ] Azure Developer CLI installed (`azd version`)
- [ ] Docker Desktop installed
- [ ] GitHub account with repository access
- [ ] Power BI workspace with XMLA endpoint enabled
- [ ] Azure OpenAI resource with deployed model
- [ ] **VNet address range confirmed** — Default: `10.1.0.0/16` (must not overlap with CGH if planned)
- [ ] **CGH team coordination** (if applicable) — Confirm their VNet range before deploying

---

## Phase 1: Project Structure Setup

### Recommended Directory Structure

```
NLtoDAX/
├── azure.yaml                    # azd project definition
├── infra/
│   ├── main.bicep               # Main orchestration
│   ├── main.parameters.json     # Environment parameters
│   ├── modules/
│   │   ├── container-apps.bicep      # Container Apps + Environment
│   │   ├── container-registry.bicep  # ACR
│   │   ├── keyvault.bicep            # Key Vault + secrets
│   │   ├── monitoring.bicep          # Log Analytics
│   │   └── app-registration.bicep    # MS Graph for App Regs (optional)
│   └── scripts/
│       └── create-app-registrations.ps1  # App reg script
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── index.html
│   ├── script.js
│   ├── styles.css
│   └── auth.js
├── backend/
│   ├── Dockerfile
│   └── ... (existing backend code)
├── app.py
├── requirements.txt
└── .github/
    └── workflows/
        └── azure-dev.yml         # CI/CD pipeline
```

### azure.yaml (Azure Developer CLI Configuration)

```yaml
name: nltodax
metadata:
  template: nltodax@1.0.0
services:
  frontend:
    project: ./frontend
    language: js
    host: containerapp
  backend:
    project: .
    language: python
    host: containerapp
infra:
  provider: bicep
  path: infra
```

---

## Phase 2: Containerization

### Frontend Dockerfile

Create `frontend/Dockerfile`:

```dockerfile
FROM nginx:alpine

# Copy static files
COPY index.html /usr/share/nginx/html/
COPY script.js /usr/share/nginx/html/
COPY styles.css /usr/share/nginx/html/
COPY auth.js /usr/share/nginx/html/

# Copy nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Expose port 80
EXPOSE 80

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
```

### Frontend nginx.conf

Create `frontend/nginx.conf`:

```nginx
events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    
    server {
        listen 80;
        server_name localhost;
        root /usr/share/nginx/html;
        index index.html;
        
        # Health check endpoint
        location /health {
            return 200 'OK';
            add_header Content-Type text/plain;
        }
        
        # Serve static files
        location / {
            try_files $uri $uri/ /index.html;
        }
        
        # Cache static assets
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
        }
    }
}
```

### Backend Dockerfile

Create `Dockerfile` in project root:

```dockerfile
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY app.py .
COPY cache/ ./cache/

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Special Consideration: ADOMD.NET

The backend currently uses .NET ADOMD client for Power BI XMLA access. Options:

| Option | Pros | Cons |
|--------|------|------|
| **Use pythonnet in Linux container** | Smaller image | Complex setup, may have issues |
| **Use Windows containers** | Works out of box | Larger images (~5GB), slower startup |
| **Migrate to Power BI REST API** | Clean, native Python | Requires code changes |

**Recommendation:** For production, consider migrating to Power BI REST API for better container compatibility.

---

## Phase 3: Infrastructure as Code (Bicep)

### Main Bicep Template

Create `infra/main.bicep`:

```bicep
targetScope = 'subscription'

@description('Name of the environment (e.g., dev, staging, prod)')
param environmentName string

@description('Primary location for all resources')
param location string = 'westus2'

@description('Power BI workspace name')
param powerBiWorkspaceName string

@description('Azure OpenAI endpoint')
param azureOpenAiEndpoint string

@description('Azure OpenAI deployment name')
param azureOpenAiDeployment string = 'gpt-5-mini'

// Generate unique suffix
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = { 'azd-env-name': environmentName, 'app': 'nltodax' }

// Resource Group
resource rg 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name: 'rg-nltodax-${environmentName}'
  location: location
  tags: tags
}

// Log Analytics Workspace
module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    tags: tags
    workspaceName: 'log-nltodax-${resourceToken}'
  }
}

// Container Registry
module acr 'modules/container-registry.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    location: location
    tags: tags
    registryName: 'acrnltodax${resourceToken}'
  }
}

// Key Vault
module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    location: location
    tags: tags
    keyVaultName: 'kv-nltodax-${resourceToken}'
  }
}

// Container Apps Environment and Apps
module containerApps 'modules/container-apps.bicep' = {
  name: 'containerApps'
  scope: rg
  params: {
    location: location
    tags: tags
    environmentName: 'cae-nltodax-${resourceToken}'
    frontendAppName: 'ca-nltodax-frontend'
    backendAppName: 'ca-nltodax-backend'
    containerRegistryName: acr.outputs.registryName
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    keyVaultName: keyVault.outputs.keyVaultName
    powerBiWorkspaceName: powerBiWorkspaceName
    azureOpenAiEndpoint: azureOpenAiEndpoint
    azureOpenAiDeployment: azureOpenAiDeployment
  }
}

// Outputs
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.loginServer
output FRONTEND_URL string = containerApps.outputs.frontendUrl
output BACKEND_URL string = containerApps.outputs.backendUrl
output AZURE_KEY_VAULT_NAME string = keyVault.outputs.keyVaultName
```

### Container Apps Module

Create `infra/modules/container-apps.bicep`:

```bicep
param location string
param tags object
param environmentName string
param frontendAppName string
param backendAppName string
param containerRegistryName string
param logAnalyticsWorkspaceId string
param keyVaultName string
param powerBiWorkspaceName string
param azureOpenAiEndpoint string
param azureOpenAiDeployment string

// Reference existing resources
resource acr 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' existing = {
  name: containerRegistryName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' existing = {
  name: keyVaultName
}

// Container Apps Environment
resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logAnalyticsWorkspaceId, '2022-10-01').customerId
        sharedKey: listKeys(logAnalyticsWorkspaceId, '2022-10-01').primarySharedKey
      }
    }
  }
}

// Backend Container App
resource backendApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: backendAppName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        corsPolicy: {
          allowedOrigins: ['*']  // Will be restricted after frontend deploys
          allowedMethods: ['GET', 'POST', 'OPTIONS']
          allowedHeaders: ['*']
          allowCredentials: true
        }
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]
      secrets: [
        {
          name: 'client-id-powerbi'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/CLIENT-ID-POWERBI'
          identity: 'system'
        }
        {
          name: 'client-secret-powerbi'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/CLIENT-SECRET-POWERBI'
          identity: 'system'
        }
        {
          name: 'client-id-openai'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/CLIENT-ID-OPENAI'
          identity: 'system'
        }
        {
          name: 'client-secret-openai'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/CLIENT-SECRET-OPENAI'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: '${acr.properties.loginServer}/nltodax-backend:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'TENANT_ID', value: subscription().tenantId }
            { name: 'CLIENT_ID_POWERBI', secretRef: 'client-id-powerbi' }
            { name: 'CLIENT_SECRET_POWERBI', secretRef: 'client-secret-powerbi' }
            { name: 'CLIENT_ID_OPENAI', secretRef: 'client-id-openai' }
            { name: 'CLIENT_SECRET_OPENAI', secretRef: 'client-secret-openai' }
            { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAiEndpoint }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }
            { name: 'WORKSPACE_NAME', value: powerBiWorkspaceName }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
}

// Frontend Container App
resource frontendApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: frontendAppName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 80
        transport: 'http'
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: '${acr.properties.loginServer}/nltodax-frontend:latest'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'API_URL', value: 'https://${backendApp.properties.configuration.ingress.fqdn}' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
      }
    }
  }
}

// Grant Key Vault access to backend app
resource keyVaultAccessPolicy 'Microsoft.KeyVault/vaults/accessPolicies@2023-02-01' = {
  parent: keyVault
  name: 'add'
  properties: {
    accessPolicies: [
      {
        tenantId: subscription().tenantId
        objectId: backendApp.identity.principalId
        permissions: {
          secrets: ['get', 'list']
        }
      }
    ]
  }
}

// Grant ACR pull access
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, backendApp.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: backendApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output frontendUrl string = 'https://${frontendApp.properties.configuration.ingress.fqdn}'
output backendUrl string = 'https://${backendApp.properties.configuration.ingress.fqdn}'
```

### Key Vault Module

Create `infra/modules/keyvault.bicep`:

```bicep
param location string
param tags object
param keyVaultName string

resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: false
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    accessPolicies: []
  }
}

output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
```

### Container Registry Module

Create `infra/modules/container-registry.bicep`:

```bicep
param location string
param tags object
param registryName string

resource acr 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

output registryName string = acr.name
output loginServer string = acr.properties.loginServer
```

### Monitoring Module

Create `infra/modules/monitoring.bicep`:

```bicep
param location string
param tags object
param workspaceName string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

output workspaceId string = logAnalytics.id
output workspaceName string = logAnalytics.name
```

---

## Phase 4: App Registrations (Programmatic)

### Required App Registrations

| App Name | Purpose | API Permissions |
|----------|---------|-----------------|
| NLtoDAX-Backend | BFF + OBO flow | Power BI: Dataset.Read.All, Workspace.Read.All |
| NLtoDAX-OpenAI | Azure OpenAI access | Cognitive Services: Cognitive Services User |

### PowerShell Script for App Registrations

Create `infra/scripts/create-app-registrations.ps1`:

```powershell
#Requires -Modules Microsoft.Graph.Applications

param(
    [Parameter(Mandatory=$true)]
    [string]$TenantId,
    
    [Parameter(Mandatory=$true)]
    [string]$KeyVaultName,
    
    [Parameter(Mandatory=$false)]
    [string]$BackendAppName = "NLtoDAX-Backend",
    
    [Parameter(Mandatory=$false)]
    [string]$OpenAIAppName = "NLtoDAX-OpenAI"
)

# Connect to Microsoft Graph
Connect-MgGraph -TenantId $TenantId -Scopes "Application.ReadWrite.All"

# Create Backend App Registration
Write-Host "Creating $BackendAppName app registration..."
$backendApp = New-MgApplication -DisplayName $BackendAppName -SignInAudience "AzureADMyOrg" -Web @{
    RedirectUris = @("https://localhost:8000/auth/callback")
}

# Create service principal for backend app
New-MgServicePrincipal -AppId $backendApp.AppId

# Create client secret for backend app
$backendSecret = Add-MgApplicationPassword -ApplicationId $backendApp.Id -PasswordCredential @{
    DisplayName = "Production Secret"
    EndDateTime = (Get-Date).AddYears(2)
}

# Add API permissions for Power BI
$powerBiApi = Get-MgServicePrincipal -Filter "displayName eq 'Power BI Service'"
$permissions = @(
    @{ Id = "7504609f-c495-4c64-8542-686125a5a36f"; Type = "Scope" }  # Dataset.Read.All
    @{ Id = "47df08d3-85e6-4bd3-8c77-680fbe28162e"; Type = "Scope" }  # Workspace.Read.All
)

Update-MgApplication -ApplicationId $backendApp.Id -RequiredResourceAccess @(
    @{
        ResourceAppId = $powerBiApi.AppId
        ResourceAccess = $permissions
    }
)

# Expose API scope for frontend
$apiScope = @{
    AdminConsentDescription = "Access NLtoDAX API"
    AdminConsentDisplayName = "Access NLtoDAX API"
    Id = [Guid]::NewGuid().ToString()
    IsEnabled = $true
    Type = "User"
    UserConsentDescription = "Access NLtoDAX API"
    UserConsentDisplayName = "Access NLtoDAX API"
    Value = "access_as_user"
}

Update-MgApplication -ApplicationId $backendApp.Id -Api @{
    Oauth2PermissionScopes = @($apiScope)
}

# Create OpenAI App Registration
Write-Host "Creating $OpenAIAppName app registration..."
$openAiApp = New-MgApplication -DisplayName $OpenAIAppName -SignInAudience "AzureADMyOrg"

New-MgServicePrincipal -AppId $openAiApp.AppId

$openAiSecret = Add-MgApplicationPassword -ApplicationId $openAiApp.Id -PasswordCredential @{
    DisplayName = "Production Secret"
    EndDateTime = (Get-Date).AddYears(2)
}

# Store secrets in Key Vault
Write-Host "Storing secrets in Key Vault..."
az keyvault secret set --vault-name $KeyVaultName --name "CLIENT-ID-POWERBI" --value $backendApp.AppId
az keyvault secret set --vault-name $KeyVaultName --name "CLIENT-SECRET-POWERBI" --value $backendSecret.SecretText
az keyvault secret set --vault-name $KeyVaultName --name "CLIENT-ID-OPENAI" --value $openAiApp.AppId
az keyvault secret set --vault-name $KeyVaultName --name "CLIENT-SECRET-OPENAI" --value $openAiSecret.SecretText

Write-Host "✅ App registrations created successfully!"
Write-Host ""
Write-Host "Backend App ID: $($backendApp.AppId)"
Write-Host "OpenAI App ID: $($openAiApp.AppId)"
Write-Host ""
Write-Host "⚠️ IMPORTANT: Grant admin consent for API permissions in Azure Portal"
```

---

## Phase 5: Secrets & Configuration Management

### Environment Variables Mapping

| Local .env Variable | Production Source | Container App Config |
|---------------------|-------------------|---------------------|
| `TENANT_ID` | Azure Subscription | Environment variable |
| `CLIENT_ID_POWERBI` | Key Vault | Secret reference |
| `CLIENT_SECRET_POWERBI` | Key Vault | Secret reference |
| `CLIENT_ID_OPENAI` | Key Vault | Secret reference |
| `CLIENT_SECRET_OPENAI` | Key Vault | Secret reference |
| `AZURE_OPENAI_ENDPOINT` | Parameter | Environment variable |
| `AZURE_OPENAI_DEPLOYMENT` | Parameter | Environment variable |
| `WORKSPACE_NAME` | Parameter | Environment variable |

### Initial Secret Population

After infrastructure deployment, populate Key Vault secrets:

```bash
# Set secrets in Key Vault
az keyvault secret set --vault-name <key-vault-name> --name "CLIENT-ID-POWERBI" --value "<value>"
az keyvault secret set --vault-name <key-vault-name> --name "CLIENT-SECRET-POWERBI" --value "<value>"
az keyvault secret set --vault-name <key-vault-name> --name "CLIENT-ID-OPENAI" --value "<value>"
az keyvault secret set --vault-name <key-vault-name> --name "CLIENT-SECRET-OPENAI" --value "<value>"
```

---

## Phase 6: CI/CD Pipeline (GitHub Actions)

### GitHub Actions Workflow

Create `.github/workflows/azure-dev.yml`:

```yaml
name: Deploy NLtoDAX to Azure

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]
  workflow_dispatch:

permissions:
  id-token: write
  contents: read

env:
  AZURE_CLIENT_ID: ${{ vars.AZURE_CLIENT_ID }}
  AZURE_TENANT_ID: ${{ vars.AZURE_TENANT_ID }}
  AZURE_SUBSCRIPTION_ID: ${{ vars.AZURE_SUBSCRIPTION_ID }}
  AZURE_CONTAINER_REGISTRY: ${{ vars.AZURE_CONTAINER_REGISTRY }}

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Log in to Azure
        uses: azure/login@v2
        with:
          client-id: ${{ env.AZURE_CLIENT_ID }}
          tenant-id: ${{ env.AZURE_TENANT_ID }}
          subscription-id: ${{ env.AZURE_SUBSCRIPTION_ID }}

      - name: Log in to Azure Container Registry
        run: az acr login --name ${{ env.AZURE_CONTAINER_REGISTRY }}

      - name: Build and push backend image
        run: |
          docker build -t ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-backend:${{ github.sha }} .
          docker push ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-backend:${{ github.sha }}
          docker tag ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-backend:${{ github.sha }} \
                     ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-backend:latest
          docker push ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-backend:latest

      - name: Build and push frontend image
        run: |
          docker build -t ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-frontend:${{ github.sha }} ./frontend
          docker push ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-frontend:${{ github.sha }}
          docker tag ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-frontend:${{ github.sha }} \
                     ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-frontend:latest
          docker push ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-frontend:latest

  deploy-staging:
    needs: build
    runs-on: ubuntu-latest
    if: github.event_name == 'push' || github.event_name == 'workflow_dispatch'
    environment: staging
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Log in to Azure
        uses: azure/login@v2
        with:
          client-id: ${{ env.AZURE_CLIENT_ID }}
          tenant-id: ${{ env.AZURE_TENANT_ID }}
          subscription-id: ${{ env.AZURE_SUBSCRIPTION_ID }}

      - name: Deploy to staging
        run: |
          az containerapp update \
            --name ca-nltodax-backend \
            --resource-group rg-nltodax-staging \
            --image ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-backend:${{ github.sha }}
          
          az containerapp update \
            --name ca-nltodax-frontend \
            --resource-group rg-nltodax-staging \
            --image ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-frontend:${{ github.sha }}

  deploy-production:
    needs: deploy-staging
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main' || github.ref == 'refs/heads/master'
    environment: production
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Log in to Azure
        uses: azure/login@v2
        with:
          client-id: ${{ env.AZURE_CLIENT_ID }}
          tenant-id: ${{ env.AZURE_TENANT_ID }}
          subscription-id: ${{ env.AZURE_SUBSCRIPTION_ID }}

      - name: Deploy to production
        run: |
          az containerapp update \
            --name ca-nltodax-backend \
            --resource-group rg-nltodax-prod \
            --image ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-backend:${{ github.sha }}
          
          az containerapp update \
            --name ca-nltodax-frontend \
            --resource-group rg-nltodax-prod \
            --image ${{ env.AZURE_CONTAINER_REGISTRY }}.azurecr.io/nltodax-frontend:${{ github.sha }}
```

### GitHub Secrets/Variables Required

Configure these in GitHub repository settings:

| Name | Type | Description |
|------|------|-------------|
| `AZURE_CLIENT_ID` | Variable | Service principal client ID |
| `AZURE_TENANT_ID` | Variable | Azure tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Variable | Azure subscription ID |
| `AZURE_CONTAINER_REGISTRY` | Variable | ACR name (without .azurecr.io) |

### Setting Up GitHub OIDC Authentication

```bash
# Create service principal with federated credentials
az ad app create --display-name "GitHub-NLtoDAX-Deploy"

# Get the app ID
APP_ID=$(az ad app list --display-name "GitHub-NLtoDAX-Deploy" --query "[0].appId" -o tsv)

# Create federated credential for main branch
az ad app federated-credential create --id $APP_ID --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<org>/<repo>:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'

# Assign Contributor role
az role assignment create --assignee $APP_ID --role "Contributor" --scope "/subscriptions/<subscription-id>"
```

---

## Phase 7: One-Click Deployment Experience

### Option A: Azure Developer CLI (Recommended)

Users can deploy with these simple commands:

```bash
# Clone the repository
git clone https://github.com/<org>/NLtoDAX.git
cd NLtoDAX

# Initialize and deploy
azd init
azd up
```

The `azd up` command will:
1. Provision all Azure infrastructure (Bicep)
2. Build Docker images
3. Push to ACR
4. Deploy to Container Apps
5. Output the URLs

### Option B: "Deploy to Azure" Button

Add to README.md:

```markdown
[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2F<org>%2FNLtoDAX%2Fmain%2Finfra%2Fazuredeploy.json)
```

This opens Azure Portal with a deployment form.

### Option C: Azure Marketplace (Enterprise)

For true "few clicks" experience:
1. Package as Azure Managed Application
2. Publish to Marketplace (public or private)
3. Users deploy from Marketplace with billing integration

---

## Phase 8: Frontend-Backend Communication

### CORS Configuration

The backend Container App is configured with CORS in the Bicep template:

```bicep
corsPolicy: {
  allowedOrigins: ['https://<frontend-fqdn>']
  allowedMethods: ['GET', 'POST', 'OPTIONS']
  allowedHeaders: ['*']
  allowCredentials: true
}
```

### API URL Configuration

The frontend receives the backend URL via environment variable:

```bicep
env: [
  { name: 'API_URL', value: 'https://${backendApp.properties.configuration.ingress.fqdn}' }
]
```

### Frontend JavaScript Update

Update `frontend/script.js` to use the environment-provided API URL:

```javascript
// Get API URL from environment or use default
const API_URL = window.ENV?.API_URL || 'http://localhost:8000';

async function sendQuestion() {
    const response = await fetch(`${API_URL}/api/ask`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${accessToken}`
        },
        body: JSON.stringify({ question })
    });
    // ...
}
```

---

## Phase 9: Monitoring & Observability

### Included Monitoring

- **Log Analytics Workspace**: Centralized container logs
- **Container Apps Metrics**: CPU, memory, request counts
- **Health Probes**: Automatic restart on failures

### Recommended Additions

1. **Application Insights** (optional):
   ```bicep
   resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
     name: 'appi-nltodax-${resourceToken}'
     location: location
     kind: 'web'
     properties: {
       Application_Type: 'web'
       WorkspaceResourceId: logAnalytics.id
     }
   }
   ```

2. **Azure Monitor Alerts**:
   - Error rate > 5%
   - Response time > 10s
   - Container restart count > 3

---

## Phase 10: Production State Management

The development `AppState` singleton (in-memory token cache, XMLA connections, shared tool instances) must be externalized for multi-replica Container Apps.

### Problem: In-Memory State Breaks with Scaling

```python
# CURRENT (app.py) — single-instance only
class AppState:
    pbi_access_token: str = None     # Lost on restart, not shared across replicas
    xmla_connection = None           # Per-process, can't share
    dax_executor = None              # Holds connection reference
```

When Container Apps scales to 2+ replicas, requests may land on different instances with different (or missing) cached tokens.

### Solution: Redis + Blob Storage

#### Redis (Azure Cache for Redis — Basic C0)
Used for fast, short-lived, frequently accessed state:

| What | TTL | Key Pattern |
|------|-----|------------|
| PBI access tokens (per user) | Token expiry (~1hr) | `obo:token:{user_oid}` |
| Workflow execution state | 30 min | `workflow:{session_id}` |
| Schema cache (hot) | 24 hr | `schema:{dataset_id}` |

```python
# Example: Replace in-memory token cache with Redis
import redis
r = redis.from_url(os.environ["REDIS_CONNECTION_STRING"])

async def get_pbi_token(user_oid: str) -> str:
    cached = r.get(f"obo:token:{user_oid}")
    if cached:
        return cached.decode()
    token = await obo_provider.get_token(user_oid)
    r.setex(f"obo:token:{user_oid}", token.expires_in, token.access_token)
    return token.access_token
```

#### Blob Storage (Azure Blob Storage — Standard LRS)
Used for large, infrequently changing data:

| What | Container | Blob Pattern |
|------|-----------|-------------|
| Schema definitions | `schema-cache` | `{dataset_id}/schema.json` |
| Chat history | `chat-history` | `{date}/chat_history.txt` |

### XMLA Connections — Per-Request Pattern

ADOMD.NET connections via pythonnet **cannot be shared across replicas**. Each request creates its own connection using the per-user OBO token fetched from Redis:

```python
# Each request gets its own short-lived XMLA connection
async def execute_dax(query: str, user_oid: str):
    token = await get_pbi_token(user_oid)  # From Redis
    connection = create_xmla_connection(token)  # Per-request
    try:
        result = connection.execute(query)
        return result
    finally:
        connection.close()  # Always close
```

### Migration Checklist

- [ ] Add `azure-redis` and `azure-storage-blob` to `requirements.txt`
- [ ] Create `backend/state/redis_store.py` — centralized Redis helpers
- [ ] Create `backend/state/blob_store.py` — schema/history persistence
- [ ] Refactor `AppState` to use Redis for tokens and Blob for schema cache
- [ ] Update `DaxQueryExecutor` to take token as parameter (not hold connection)
- [ ] Add Redis and Blob connection strings to Key Vault + env vars
- [ ] Test with 2 replicas to verify no state leakage

---

## Implementation Roadmap

| Phase | Task | Effort | Priority | Citadel |
|-------|------|--------|----------|---------|
| **1** | Create Dockerfiles (frontend + backend) | 2 hours | High | — |
| **2** | Create Bicep modules (infra) | 4-6 hours | High | — |
| **2b** | VNet + subnet design (10.1.0.0/16) | 1-2 hours | High | CAS |
| **3** | Create azure.yaml for azd | 1 hour | High | — |
| **4** | Set up GitHub Actions workflow | 2-3 hours | High | — |
| **5** | App registration automation script | 2 hours | Medium | — |
| **6** | Key Vault integration | 2 hours | High | CAS |
| **7** | Update backend for production (CORS, health) | 2-3 hours | High | — |
| **8** | Redis + Blob Storage integration | 4-6 hours | High | CAS |
| **9** | Private endpoints + Private DNS Zones | 2-3 hours | High | CAS |
| **10** | OpenTelemetry instrumentation | 3-4 hours | Medium | CAS→CGH |
| **11** | Create AI Access Contract YAML | 30 min | Medium | CAS→CGH |
| **12** | Create "Deploy to Azure" button | 1 hour | Medium | — |
| **13** | Documentation & README updates | 2 hours | Medium | — |
| **14** | Testing & validation (2+ replicas) | 4 hours | High | — |

**Total Estimated Effort: 28-36 hours**

### Citadel-Specific Tasks (for CGH readiness)

| Task | Effort | When |
|------|--------|------|
| Confirm VNet address space with CGH team | Async | Before Phase 2b |
| Make `LLM_ENDPOINT` env var configurable | 30 min | Phase 7 |
| Instrument workflow with OpenTelemetry spans | 3-4 hrs | Phase 10 |
| Prepare `citadel-ai-access-contract.yaml` | 30 min | Phase 11 |
| Test with 2+ replicas (state externalized) | 2 hrs | Phase 14 |

---

## Future: CGH Integration (Phase 2)

When the Citadel Governance Hub (CGH) is deployed by the platform team, the CAS integrates via VNet peering. **This is a configuration exercise, not a re-architecture.**

### Integration Steps

| Step | Action | Effort | Code Change |
|------|--------|--------|-------------|
| 1 | **VNet peering**: Peer CAS VNet (10.1.0.0/16) ↔ CGH VNet (10.0.0.0/16) | 15 min | None (infra) |
| 2 | **DNS resolution**: Add Private DNS Zone links for CGH services | 15 min | None (infra) |
| 3 | **Re-route LLM traffic**: Change `LLM_ENDPOINT` env var to APIM AI Gateway URL | 5 min | None (env var) |
| 4 | **Submit AI Access Contract**: Provide `citadel-ai-access-contract.yaml` to CGH team | Async | None |
| 5 | **Forward telemetry**: Add diagnostic settings to send App Insights → CGH Log Analytics | 15 min | None (infra) |
| 6 | **Register agent**: Register NLtoDAX in CGH's API Center / AI Registry | 15 min | None |
| 7 | **Enable Defender for Cloud**: CGH team enables Defender for the CAS subscription | Async | None |
| 8 | **Purview integration**: Tag data flows in Microsoft Purview for compliance | 30 min | None |

**Total integration effort: ~1-2 hours** (plus async approvals)

### What Changes When CGH Connects

```
BEFORE (Phase 1):
  Backend → Azure OpenAI (direct, via LLM_ENDPOINT)

AFTER (Phase 2):
  Backend → APIM AI Gateway (via LLM_ENDPOINT) → Content Safety → Azure OpenAI
```

- **Code changes**: Zero — only `LLM_ENDPOINT` env var changes
- **Auth changes**: None — APIM AI Gateway passes through the same Entra token
- **New capabilities gained**: Content filtering, prompt shields, usage/cost tracking, central logging, API catalog listing

### CGH Integration Checklist

- [ ] Confirm CGH VNet address range (default: `10.0.0.0/16`)
- [ ] Establish VNet peering (bidirectional, allow gateway transit if needed)
- [ ] Link CGH Private DNS Zones to CAS VNet
- [ ] Update `LLM_ENDPOINT` to `https://apim-citadel-hub.azure-api.net/openai`
- [ ] Submit AI Access Contract to CGH platform team
- [ ] Add diagnostic settings: App Insights → CGH Log Analytics workspace
- [ ] Verify DAX queries still work end-to-end through the gateway
- [ ] Register NLtoDAX in API Center

---

## Quick Reference Commands

```bash
# Initial deployment
azd init
azd up

# Update after code changes
azd deploy

# View logs
az containerapp logs show --name ca-nltodax-backend --resource-group rg-nltodax-prod --follow

# Scale manually
az containerapp update --name ca-nltodax-backend --resource-group rg-nltodax-prod --min-replicas 2 --max-replicas 20

# View secrets
az keyvault secret list --vault-name <key-vault-name>

# Restart container app
az containerapp revision restart --name ca-nltodax-backend --resource-group rg-nltodax-prod --revision <revision-name>
```

---

## Support & Troubleshooting

### Common Issues

1. **Container fails to start**
   - Check logs: `az containerapp logs show --name <app> --resource-group <rg>`
   - Verify secrets are populated in Key Vault
   - Check health probe endpoints

2. **CORS errors**
   - Verify CORS policy in Container App config
   - Check frontend is using correct API_URL

3. **Authentication failures**
   - Verify app registrations have correct permissions
   - Check admin consent was granted
   - Verify secrets match between Key Vault and app registrations

4. **Power BI connection fails**
   - Verify workspace name is correct
   - Check service principal has workspace access
   - Verify XMLA endpoint is enabled on workspace

---

*Last updated: February 2026 — Updated to reflect Citadel Agent Spoke (CAS) architecture and CGH integration plan*
