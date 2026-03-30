# ── Monkey-patch: opentelemetry-semantic-conventions-ai >=0.4.13 moved several
#    LLM_* attributes to the standard gen_ai.* namespace.  agent-framework still
#    references the old names → add them back so the import chain doesn't break.
from opentelemetry.semconv_ai import SpanAttributes as _SA  # noqa: E402
_MISSING = {
    "LLM_SYSTEM": "gen_ai.system",
    "LLM_REQUEST_MODEL": "gen_ai.request.model",
    "LLM_REQUEST_MAX_TOKENS": "gen_ai.request.max_tokens",
    "LLM_REQUEST_TEMPERATURE": "gen_ai.request.temperature",
    "LLM_REQUEST_TOP_P": "gen_ai.request.top_p",
    "LLM_RESPONSE_MODEL": "gen_ai.response.model",
    "LLM_TOKEN_TYPE": "gen_ai.token.type",
}
for _attr, _val in _MISSING.items():
    if not hasattr(_SA, _attr):
        setattr(_SA, _attr, _val)
del _SA, _MISSING, _attr, _val

"""
FastAPI Backend for Natural Language to DAX Query Generator

This API provides endpoints for:
- Asking questions in natural language
- Processing queries through the NL-to-DAX workflow
- The workflow uses Azure AI Agent Service with sequential steps

The workflow architecture:
1. Extract Schema (executor node) - fetches Power BI metadata
2. Format Schema (executor node) - formats for LLM context
3. DAX Agent (agent node) - generates and executes DAX queries
"""

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import csv
import asyncio
from pathlib import Path
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import workflow from backend folder
from backend.agent_workflow import (
    create_dax_workflow,
    run_pipeline_sync,
    DAXAgentConfig,
)
from backend.tools.auth import load_environment, AzureOpenAIConfig
from backend.auth import (
    AuthConfig,
    TokenValidator,
    TokenValidationError,
    OBOTokenProvider,
    OBOTokenError,
    exchange_for_powerbi_token,
)


# ============================================================
# Pydantic Models
# ============================================================

class QueryRequest(BaseModel):
    """Request model for natural language queries."""
    question: str = Field(..., description="Natural language question about the data")
    include_explanation: bool = Field(
        default=True, 
        description="Include explanation of the generated DAX query"
    )
    include_raw_dax: bool = Field(
        default=False,
        description="Include raw DAX query in response"
    )


class QueryResponse(BaseModel):
    """Response model for query results."""
    success: bool = Field(..., description="Whether the query was processed successfully")
    question: str = Field(..., description="The original question")
    formatted_answer: Optional[str] = Field(
        default=None,
        description="LLM-formatted answer with markdown"
    )
    summary: Optional[str] = Field(
        default=None,
        description="Brief summary of the results"
    )
    raw_dax: Optional[str] = Field(
        default=None,
        description="Raw DAX query if requested"
    )
    rows_returned: Optional[int] = Field(
        default=None,
        description="Number of rows returned by the query"
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if query failed"
    )
    execution_time_ms: float = Field(..., description="Total execution time in milliseconds")
    chart_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Chart.js configuration for visualization"
    )
    chart_type: Optional[str] = Field(
        default=None,
        description="Type of chart (bar, line, none, etc.)"
    )
    requires_reauth: bool = Field(
        default=False,
        description="True if authentication token expired and user needs to re-authenticate"
    )
    dax_generation_ttft: Optional[float] = Field(
        default=None,
        description="Time to First Token for DAX generation (seconds)"
    )
    dax_generation_ttlt: Optional[float] = Field(
        default=None,
        description="Time to Last Token for DAX generation (seconds)"
    )
    timing: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Structured pipeline timing breakdown"
    )
    clarification_needed: bool = Field(
        default=False,
        description="True if the question is ambiguous and needs clarification"
    )
    clarification_message: Optional[str] = Field(
        default=None,
        description="Message explaining what is ambiguous"
    )
    clarification_suggestions: Optional[list] = Field(
        default=None,
        description="List of suggested rephrased questions the user can click"
    )


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str = Field(..., description="Health status")
    initialized: bool = Field(..., description="Whether the service is fully initialized")
    timestamp: str = Field(..., description="Current timestamp")


class StatusResponse(BaseModel):
    """Response model for status check."""
    status: str = Field(..., description="Current status")
    schema_cached: bool = Field(..., description="Whether schema is cached")
    token_cached: bool = Field(..., description="Whether token is cached")
    schema_cache_date: Optional[str] = Field(
        default=None,
        description="Date of cached schema"
    )
    initialized: bool = Field(..., description="Whether service is initialized")
    message: str = Field(..., description="Status message")
    workflow_type: str = Field(default="azure_agent", description="Type of workflow in use")


# ============================================================
# Global State
# ============================================================

class AppState:
    """Manages application state and resources."""
    
    def __init__(self):
        self.initialized = False
        self.workflow = None  # Development workflow
        self.shared_instances = None  # Shared tool instances
        self.token_validator: Optional[TokenValidator] = None  # Validates frontend tokens
        self.obo_provider: Optional[OBOTokenProvider] = None  # Exchanges tokens for Power BI
        self.auth_config: Optional[AuthConfig] = None  # Config for frontend MSAL.js
        self.config: Optional[AzureOpenAIConfig] = None
        self.schema_cache_file: Optional[Path] = None
        
        # Connection details for workflow refresh
        self.workspace_name: str = ""
        self.database_name: str = ""
        
        # Cached Power BI token (from /auth/initialize)
        # Reused in /query to avoid redundant OBO exchanges
        self._cached_powerbi_token: Optional[str] = None
        self._cached_powerbi_token_expires_at: int = 0  # Unix timestamp
    
    def get_cached_powerbi_token(self) -> Optional[str]:
        """Get the cached PBI token if it's still valid (with 5 min buffer)."""
        import time
        if self._cached_powerbi_token and time.time() < (self._cached_powerbi_token_expires_at - 300):
            return self._cached_powerbi_token
        return None
    
    def set_cached_powerbi_token(self, token: str, expires_in: int = 3600):
        """Cache the PBI token with its expiry."""
        import time
        self._cached_powerbi_token = token
        self._cached_powerbi_token_expires_at = int(time.time()) + expires_in
        logger.info(f"[AUTH] PBI token cached (expires in {expires_in}s)")
    
    def clear_cached_powerbi_token(self):
        """Clear the cached PBI token (e.g., on auth error)."""
        self._cached_powerbi_token = None
        self._cached_powerbi_token_expires_at = 0
    
    async def refresh_workflow(self, access_token: str = None):
        """Refresh the workflow with new token from frontend."""
        logger.info("Refreshing workflow...")
        
        # Recreate workflow and shared instances
        self.workflow, self.shared_instances = create_dax_workflow(pre_connect_powerbi=True)
        
        logger.info("[OK] Workflow refreshed")


app_state = AppState()


# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(
    title="Natural Language to DAX Query Generator",
    description="Convert natural language questions to DAX queries using Azure AI Agent Service workflow",
    version="2.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Startup/Shutdown Events
# ============================================================

@app.on_event("startup")
async def startup_event():
    """Initialize application resources on startup."""
    logger.info("Starting application initialization...")
    logger.info("Using Development Agent Workflow architecture")
    logger.info("🔐 BFF + OBO authentication flow enabled")
    
    try:
        # Load environment variables
        tenant_id, client_id_powerbi, client_secret_powerbi, workspace_name, database_name, adomd_dll = load_environment()
        
        # Store configuration
        app_state.workspace_name = workspace_name
        app_state.database_name = database_name
        
        # Initialize auth configuration for frontend MSAL.js
        # Frontend will request token for backend API (not Power BI)
        app_state.auth_config = AuthConfig.from_env(redirect_uri="http://localhost:8000")
        logger.info(f"[OK] Auth configuration initialized - API scope: {app_state.auth_config.scopes}")
        
        # Initialize token validator (validates API tokens from frontend)
        app_state.token_validator = TokenValidator(
            tenant_id=tenant_id,
            client_id=client_id_powerbi
        )
        logger.info("[OK] Token validator initialized (validates API audience only)")
        
        # Initialize OBO provider (exchanges API token for Power BI token)
        app_state.obo_provider = OBOTokenProvider(
            tenant_id=tenant_id,
            client_id=client_id_powerbi,
            client_secret=client_secret_powerbi,
        )
        logger.info("[OK] OBO provider initialized (will exchange tokens for Power BI)")
        
        # Initialize Azure OpenAI config
        app_state.config = AzureOpenAIConfig()
        app_state.config.validate()
        logger.info("[OK] Azure OpenAI configuration validated")
        
        # Pre-warm Azure OpenAI token to avoid delay on first request
        from backend.tools.auth import AzureOpenAIAuthProvider
        AzureOpenAIAuthProvider.prewarm_token(config=app_state.config)
        
        # Create workflow and shared instances
        # REST API — no XMLA connection needed, token set on first auth
        logger.info("Using Power BI REST API (no XMLA connection needed)")
        app_state.workflow, app_state.shared_instances = create_dax_workflow(pre_connect_powerbi=False)
        
        logger.info("[OK] Workflow and shared instances initialized")
        
        # Mark as initialized
        app_state.initialized = True
        logger.info("✓ Application fully initialized (awaiting user authentication)")
        
    except Exception as e:
        logger.error(f"✗ Startup failed: {str(e)}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    logger.info("Shutting down application...")
    logger.info("✓ Application shutdown complete")


# ============================================================
# API Endpoints
# ============================================================

@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="healthy" if app_state.initialized else "initializing",
        initialized=app_state.initialized,
        timestamp=datetime.now().isoformat()
    )


@app.get("/status", response_model=StatusResponse)
async def status() -> StatusResponse:
    """Get detailed status of the service."""
    schema_cache_exists = (
        app_state.schema_cache_file.exists() 
        if app_state.schema_cache_file 
        else False
    )
    
    return StatusResponse(
        status="ready" if app_state.initialized else "initializing",
        schema_cached=schema_cache_exists,
        token_cached=False,  # Tokens are now managed by frontend MSAL.js
        schema_cache_date=(
            app_state.schema_cache_file.name.replace("schema_pack_", "").replace(".txt", "")
            if app_state.schema_cache_file and schema_cache_exists
            else None
        ),
        initialized=app_state.initialized,
        message="Service is ready to process queries" if app_state.initialized else "Service is initializing",
        workflow_type="development_workflow"
    )


@app.get("/auth/config")
async def get_auth_config():
    """
    Get MSAL configuration for frontend authentication.
    
    Returns the configuration needed by MSAL.js to authenticate users.
    No sensitive data (like client_secret) is exposed.
    """
    if not app_state.auth_config:
        raise HTTPException(
            status_code=503,
            detail="Auth configuration not initialized"
        )
    
    return app_state.auth_config.to_dict()


@app.post("/auth/initialize")
def initialize_auth(
    authorization: Optional[str] = Header(None)
):
    """
    Initialize Power BI access right after user authenticates.
    
    Called by frontend immediately after MSAL login succeeds.
    Performs OBO token exchange and caches the Power BI token.
    
    With REST API, no XMLA connection is needed — the token is simply
    stored for use in subsequent /query calls.
    """
    # Validate Bearer token
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header with Bearer token is required"
        )
    
    user_api_token = authorization.replace("Bearer ", "")
    
    # Validate the token
    try:
        token_claims = app_state.token_validator.validate_token(user_api_token)
        logger.info(f"[AUTH INIT] User authenticated: {token_claims.preferred_username or token_claims.email}")
    except TokenValidationError as e:
        logger.warning(f"[AUTH INIT] Token validation failed: {e}")
        raise HTTPException(status_code=401, detail=str(e))
    
    # Exchange user's API token for Power BI token via OBO
    try:
        obo_result = app_state.obo_provider.exchange_token_for_powerbi(
            user_assertion=user_api_token,
            user_id=token_claims.user_id,
        )
        powerbi_token = obo_result.access_token
        # Cache the token on app_state so /query reuses the EXACT same string
        expires_in = obo_result.expires_at - int(__import__('time').time())
        app_state.set_cached_powerbi_token(powerbi_token, expires_in=max(expires_in, 300))
        logger.info("[AUTH INIT] OBO exchange successful - Power BI token acquired and cached")
    except OBOTokenError as e:
        logger.error(f"[AUTH INIT] OBO token exchange failed: {e}")
        raise HTTPException(
            status_code=401,
            detail=f"Failed to acquire Power BI access: {str(e)}"
        )
    
    # Set token on shared DAXExecutor (REST API — no connection to establish)
    try:
        executor = app_state.shared_instances.get("dax_executor") if app_state.shared_instances else None
        if executor is None:
            from backend.tools.execute_dax import get_executor
            executor = get_executor()
            if app_state.shared_instances:
                app_state.shared_instances["dax_executor"] = executor
        
        executor.set_access_token(powerbi_token)
        print("[AUTH INIT] Token set on executor")
        
        # Warm up: run a trivial DAX query to wake the dataset engine.
        # Power BI loads the dataset model into memory on first query,
        # which can take 5-10s on shared/Pro capacity.  By doing it here
        # (right after login), the user's first real query is fast.
        try:
            warmup_start = __import__('time').time()
            print("[AUTH INIT] Running warm-up query to wake dataset...")
            warmup_result = executor.execute('EVALUATE ROW("x", 1)')
            warmup_elapsed = __import__('time').time() - warmup_start
            if warmup_result.success:
                print(f"[AUTH INIT] Dataset warm-up complete ({warmup_elapsed:.2f}s)")
            else:
                print(f"[AUTH INIT] Warm-up query error: {warmup_result.error}")
        except Exception as warmup_err:
            print(f"[AUTH INIT] Warm-up failed: {warmup_err}")
        
        return {
            "success": True,
            "message": "Power BI access initialized (REST API)",
            "user": token_claims.preferred_username or token_claims.email,
        }
        
    except Exception as e:
        logger.warning(f"[AUTH INIT] Token setup warning: {e}")
        # Don't fail — token will be passed per-query anyway
        return {
            "success": True,
            "message": "Authentication successful",
            "user": token_claims.preferred_username or token_claims.email,
            "warning": str(e),
        }


@app.get("/evaluations/questions")
async def get_ground_truth_questions():
    """
    Return the list of ground-truth evaluation questions from the CSV.
    
    These are used by the UI to let users pick a pre-defined question
    from a dropdown in the sidebar.
    """
    csv_path = Path(__file__).parent / "backend" / "evaluations" / "ui_example_questions.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail="UI example questions file not found")
    
    questions = []
    with open(csv_path, "r", encoding="utf-8") as f:
        for line in f:
            q = line.strip()
            if q:
                questions.append(q)
    
    return {"questions": questions, "count": len(questions)}


@app.post("/query", response_model=QueryResponse)
def process_query(
    request: QueryRequest,
    authorization: Optional[str] = Header(None)
) -> QueryResponse:
    """
    Process a natural language question through the workflow.
    
    Requires a valid Bearer token from frontend MSAL.js.
    Uses BFF + OBO pattern to exchange user's API token for Power BI token.
    
    Uses 'def' (not async) so FastAPI runs it in a thread pool,
    allowing the blocking workflow to run without freezing the event loop.
    
    The workflow executes these steps:
    1. ExtractIntent - Identify data domain and extract intent
    2. GenerateDAX - Generate DAX query using LLM
    3. ValidateDAX - Validate and improve DAX query
    4. ExecuteDAX - Execute against Power BI
    5. FormatResults - Format results into human-readable text
    """
    start_time = datetime.now()
    
    # Validate Bearer token
    if not authorization or not authorization.startswith("Bearer "):
        logger.warning("Query received without valid Authorization header")
        raise HTTPException(
            status_code=401,
            detail="Authorization header with Bearer token is required"
        )
    
    user_api_token = authorization.replace("Bearer ", "")
    
    # Validate the token (must have audience = backend API)
    try:
        token_claims = app_state.token_validator.validate_token(user_api_token)
        logger.info(f"Request authenticated for user: {token_claims.preferred_username or token_claims.email}")
    except TokenValidationError as e:
        logger.warning(f"Token validation failed: {e}")
        raise HTTPException(
            status_code=401,
            detail=str(e)
        )
    
    # Get Power BI token - reuse the cached token from /auth/initialize if still valid
    # This avoids a redundant OBO exchange on each query
    powerbi_token = app_state.get_cached_powerbi_token()
    if powerbi_token:
        logger.info("Using cached Power BI token (from auth/initialize) - connection stays warm")
    else:
        # Cache expired or /auth/initialize didn't run - do fresh OBO exchange
        try:
            obo_result = app_state.obo_provider.exchange_token_for_powerbi(
                user_assertion=user_api_token,
                user_id=token_claims.user_id,
            )
            powerbi_token = obo_result.access_token
            # Cache for future queries
            expires_in = obo_result.expires_at - int(__import__('time').time())
            app_state.set_cached_powerbi_token(powerbi_token, expires_in=max(expires_in, 300))
            logger.info("Fresh OBO exchange - Power BI token acquired and cached")
            
            # Set on executor since this is a new token
            try:
                executor = app_state.shared_instances.get("dax_executor") if app_state.shared_instances else None
                if executor:
                    executor.set_access_token(powerbi_token)
            except Exception as e:
                logger.warning(f"Could not set token on executor: {e}")
        except OBOTokenError as e:
            logger.error(f"OBO token exchange failed: {e}")
            raise HTTPException(
                status_code=401,
                detail=f"Failed to acquire Power BI access: {str(e)}"
            )
    
    # Check if service is initialized
    if not app_state.initialized:
        logger.warning("Query received but service not initialized")
        raise HTTPException(
            status_code=503,
            detail="Service is still initializing. Please try again in a moment."
        )
    
    try:
        logger.info(f"Processing query through LLM Planner pipeline: {request.question}")
        
        # Process through LLM Planner → Analyst → Format pipeline
        result = run_pipeline_sync(
            shared=app_state.shared_instances,
            workflow=app_state.workflow,
            user_query=request.question,
            access_token=powerbi_token,  # Pass OBO-acquired Power BI token
            timeout=120,
        )
        
        # Handle token expiration errors
        if not result.get("success") and result.get("error"):
            error_str = str(result.get("error", "")).lower()
            token_keywords = ['401', 'unauthorized', 'token', 'expired', 'authentication']
            
            if any(keyword in error_str for keyword in token_keywords):
                logger.warning("Token error detected - user may need to re-authenticate")
                # Clear OBO cache for this user
                app_state.obo_provider.clear_cache(token_claims.user_id)
                app_state.clear_cached_powerbi_token()
                # Return requires_reauth flag to frontend
                execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
                return QueryResponse(
                    success=False,
                    question=request.question,
                    error="Authentication token expired. Please sign in again.",
                    execution_time_ms=execution_time_ms,
                    requires_reauth=True
                )
        
        execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
        
        logger.info(f"Query processed in {execution_time_ms:.2f}ms - Success: {result.get('success')}")
        logger.info(f"[DEBUG] chart_config present: {result.get('chart_config') is not None}")
        logger.info(f"[DEBUG] chart_type: {result.get('chart_type', 'none')}")
        
        # Check if re-authentication is required
        requires_reauth = result.get("requires_reauth", False)
        
        # Check if clarification is needed
        clarification_needed = result.get("clarification_needed", False)
        
        return QueryResponse(
            success=result.get("success", False),
            question=request.question,
            formatted_answer=result.get("formatted_answer"),
            summary=None,
            raw_dax=result.get("dax_query"),  # Always include DAX for collapsible toggle
            rows_returned=result.get("row_count"),  # Use row_count from executor output
            error=result.get("error"),
            execution_time_ms=execution_time_ms,
            chart_config=result.get("chart_config"),
            chart_type=result.get("chart_type", "none"),
            requires_reauth=requires_reauth,
            dax_generation_ttft=result.get("dax_generation_ttft"),
            dax_generation_ttlt=result.get("dax_generation_ttlt"),
            timing=result.get("timing"),
            clarification_needed=clarification_needed,
            clarification_message=result.get("clarification_message"),
            clarification_suggestions=result.get("clarification_suggestions"),
        )
    
    except Exception as e:
        execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
        error_msg = str(e)
        logger.error(f"Query processing failed: {error_msg}")
        
        # Check if the exception is authentication-related
        error_lower = error_msg.lower()
        auth_keywords = ['authentication failed', 'token expired', 'unauthorized', '401', 'access denied']
        requires_reauth = any(kw in error_lower for kw in auth_keywords)
        
        return QueryResponse(
            success=False,
            question=request.question,
            formatted_answer=None,
            summary=None,
            raw_dax=None,
            rows_returned=None,
            error="Your authentication session has expired. Please refresh the page to re-authenticate." if requires_reauth else error_msg,
            execution_time_ms=execution_time_ms,
            chart_config=None,
            chart_type="none",
            requires_reauth=requires_reauth,
        )


# ============================================================
# Static Files & Frontend
# ============================================================

frontend_dir = Path(__file__).resolve().parent / "frontend"
frontend_dir.mkdir(exist_ok=True)
logger.info(f"Frontend directory: {frontend_dir} (exists={frontend_dir.exists()})")

app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def root():
    """Serve the main frontend page."""
    frontend_file = frontend_dir / "index.html"
    
    if frontend_file.exists():
        return FileResponse(str(frontend_file), media_type="text/html")
    else:
        logger.warning(f"index.html not found at {frontend_file}")
        return {
            "message": "Welcome to Natural Language to DAX Query Generator",
            "version": "2.0.0 - Azure AI Agent Service",
            "documentation": "/docs",
            "workflow": {
                "step_1": "Extract Schema (executor node)",
                "step_2": "Format Schema (executor node)",
                "step_3": "DAX Agent (tools: generate_dax, execute_dax)"
            },
            "endpoints": {
                "health": "/health",
                "status": "/status",
                "query": "/query (POST)"
            }
        }


@app.get("/auth.js")
async def serve_auth_js():
    """Serve the auth.js file directly (no-cache to pick up latest)."""
    auth_file = frontend_dir / "auth.js"
    if auth_file.exists():
        return FileResponse(
            auth_file,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
        )
    raise HTTPException(status_code=404, detail="auth.js not found")


@app.get("/styles.css")
async def serve_styles():
    """Serve the styles.css file directly."""
    styles_file = frontend_dir / "styles.css"
    if styles_file.exists():
        return FileResponse(
            styles_file,
            media_type="text/css",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
        )
    raise HTTPException(status_code=404, detail="styles.css not found")


@app.get("/script.js")
async def serve_script():
    """Serve the script.js file directly."""
    script_file = frontend_dir / "script.js"
    if script_file.exists():
        return FileResponse(
            script_file,
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
        )
    raise HTTPException(status_code=404, detail="script.js not found")


# ============================================================
# Error Handlers
# ============================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handle HTTP exceptions."""
    logger.error(f"HTTP Exception: {exc.status_code} - {exc.detail}")
    return {
        "success": False,
        "error": exc.detail,
        "status_code": exc.status_code
    }


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle general exceptions."""
    logger.error(f"Unhandled Exception: {str(exc)}")
    return {
        "success": False,
        "error": "An unexpected error occurred. Please check the server logs.",
        "status_code": 500
    }


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")