"""
Execute DAX Tool — Power BI REST API

Executes DAX queries against Power BI datasets using the Execute Queries REST API.
No ADOMD.NET, no pythonnet, no COM — pure HTTP via httpx.

API endpoint:
    POST https://api.powerbi.com/v1.0/myorg/groups/{workspaceId}/datasets/{datasetId}/executeQueries

Authentication: Uses Power BI access token (from OBO exchange)
Scope: https://analysis.windows.net/powerbi/api/.default  (same as XMLA)

Limits (Power BI REST API):
    - Max 100,000 rows per query
    - Max 10 MB response payload
    - Max 5 queries per batch (we send 1)

Usage:
    from backend.tools.execute_dax import execute_dax, get_executor

    # Simple function call
    result = execute_dax("EVALUATE SUMMARIZE(DimDate, DimDate[Year])")

    # Or use the executor directly
    executor = get_executor()
    result = executor.execute("EVALUATE ...", access_token="eyJ...")
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import httpx
from dotenv import load_dotenv

# Get project root for .env file
_project_root = Path(__file__).resolve().parent.parent.parent

# Add project root to path for imports
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Load environment variables
load_dotenv(_project_root / ".env")

logger = logging.getLogger(__name__)

# Power BI REST API base URL
POWERBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"


# ============================================================================
# EXECUTION RESULT (same interface as XMLA version)
# ============================================================================

@dataclass
class ExecutionResult:
    """Result of a DAX query execution."""
    success: bool = False
    columns: List[str] = field(default_factory=list)
    data: List[List[Any]] = field(default_factory=list)
    row_count: int = 0
    error: Optional[str] = None
    auth_message: Optional[str] = None
    requires_reauth: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        result = {
            "success": self.success,
            "columns": self.columns,
            "data": self.data,
            "row_count": self.row_count,
        }
        if self.error:
            result["error"] = self.error
        if self.auth_message:
            result["auth_message"] = self.auth_message
        if self.requires_reauth:
            result["requires_reauth"] = self.requires_reauth
        return result

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)


# ============================================================================
# DAX EXECUTOR — Power BI REST API
# ============================================================================

class DAXExecutor:
    """
    Executes DAX queries against Power BI using the REST API.

    No thread affinity, no COM — pure HTTP via httpx.
    Uses a persistent httpx.Client for connection pooling and keep-alive,
    so the TLS handshake + DNS resolution only happens once.

    Authentication:
        - Pass access_token per call, or set it via set_access_token()
        - Token must have scope: https://analysis.windows.net/powerbi/api/.default
    """

    def __init__(self, workspace_id: str = None, dataset_id: str = None):
        """
        Initialize the executor.

        Creates a persistent httpx.Client with connection pooling.
        The first request establishes TLS; subsequent requests reuse the connection.

        Args:
            workspace_id: Power BI workspace GUID (defaults to WORKSPACE_ID env var)
            dataset_id: Power BI dataset GUID (defaults to DATASET_ID env var)
        """
        self._workspace_id = workspace_id or os.getenv("WORKSPACE_ID")
        self._dataset_id = dataset_id or os.getenv("DATASET_ID")
        self._user_access_token: Optional[str] = None

        # For compatibility with code that checks _connected
        self._connected = True  # REST API is always "connected"

        if not self._workspace_id:
            raise RuntimeError("WORKSPACE_ID environment variable is required")

        if not self._dataset_id:
            raise RuntimeError("DATASET_ID environment variable is required")

        # Build the execute queries URL
        self._api_url = self._build_url(self._dataset_id)

        # Persistent HTTP client — reuses TCP connections + TLS sessions
        self._client = httpx.Client(
            timeout=120,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        print(f"[REST API] Executor ready: workspace={self._workspace_id}, dataset={self._dataset_id}")

    def _build_url(self, dataset_id: str) -> str:
        """Build the Execute Queries API URL."""
        return f"{POWERBI_API_BASE}/groups/{self._workspace_id}/datasets/{dataset_id}/executeQueries"

    def set_access_token(self, access_token: str) -> None:
        """
        Set the user's access token.

        Unlike the XMLA version, this does NOT trigger a reconnect.
        The token is simply stored and used on the next execute() call.

        Args:
            access_token: User's Power BI access token (from OBO exchange)
        """
        if self._user_access_token != access_token:
            self._user_access_token = access_token
            print(f"[AUTH] Token updated (REST API - no reconnect needed)")
        else:
            print(f"[AUTH] Token already set and matches")

    def _ensure_connection(self) -> None:
        """
        No-op for REST API compatibility.

        The XMLA version used this to establish a persistent connection.
        The REST API is stateless — nothing to connect to.
        Kept for interface compatibility with app.py and executor code.
        """
        print(f"[REST API] _ensure_connection() called (no-op for REST API)")
        return None

    def execute(self, dax_query: str, access_token: str = None) -> ExecutionResult:
        """
        Execute a DAX query via Power BI REST API.

        Args:
            dax_query: The DAX EVALUATE statement to execute
            access_token: Power BI access token (overrides stored token)

        Returns:
            ExecutionResult with columns, data, and row count or error
        """
        token = access_token or self._user_access_token
        if not token:
            return ExecutionResult(
                success=False,
                error="No access token provided. User must authenticate via frontend.",
                requires_reauth=True,
            )

        # Build request body
        request_body = {
            "queries": [{"query": dax_query}],
            "serializerSettings": {"includeNulls": True},
        }

        AUTHENTICATION_ERROR_PATTERNS = [
            "authentication failed",
            "token expired",
            "token is expired",
            "invalid token",
            "unauthorized",
            "access denied",
            "not authenticated",
        ]

        def is_authentication_error(status_code: int, error_msg: str) -> bool:
            if status_code in (401, 403):
                return True
            error_lower = error_msg.lower()
            return any(p in error_lower for p in AUTHENTICATION_ERROR_PATTERNS)

        max_retries = 2

        for attempt in range(max_retries):
            try:
                resp = self._client.post(
                    self._api_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )

                # Handle HTTP errors
                if resp.status_code != 200:
                    raw_body = resp.text
                    print(f"[REST API] Raw error response body: {raw_body[:1000]}")
                    error_text = raw_body
                    try:
                        error_json = resp.json()
                        error_detail = error_json.get("error", {})
                        # Power BI REST API may nest error differently
                        error_text = (
                            error_detail.get("message")
                            or error_detail.get("code")
                            or error_json.get("message")
                            or raw_body[:500]
                        )
                    except Exception:
                        error_text = raw_body[:500]
                    
                    print(f"[REST API] Error {resp.status_code}: {error_text}")

                    if is_authentication_error(resp.status_code, error_text):
                        print(f"[REST API] Authentication error: {resp.status_code}")
                        return ExecutionResult(
                            success=False,
                            error="Your authentication session has expired. Please refresh the page and try again to re-authenticate.",
                            requires_reauth=True,
                        )

                    # Retry on transient errors (429, 5xx)
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                        retry_after = int(resp.headers.get("Retry-After", 2))
                        print(f"[REST API] Transient error {resp.status_code}, retrying after {retry_after}s...")
                        time.sleep(retry_after)
                        continue

                    return ExecutionResult(
                        success=False,
                        error=f"Power BI API error ({resp.status_code}): {error_text}",
                    )

                # Parse successful response
                result_json = resp.json()
                
                # Power BI executeQueries may return 200 with errors inside the response body
                if "error" in result_json:
                    err = result_json["error"]
                    error_text = err.get("message") or err.get("code") or str(err)
                    print(f"[REST API] Error in response body: {error_text}")
                    return ExecutionResult(success=False, error=f"Power BI error: {error_text}")
                
                # Check for errors inside results array
                results = result_json.get("results", [])
                if results and "error" in results[0]:
                    err = results[0]["error"]
                    error_text = err.get("message") or err.get("code") or str(err)
                    print(f"[REST API] Error in results[0]: {error_text}")
                    return ExecutionResult(success=False, error=f"DAX error: {error_text}")
                return self._parse_response(result_json)

            except httpx.TimeoutException:
                if attempt < max_retries - 1:
                    print(f"[REST API] Timeout, retrying...")
                    continue
                return ExecutionResult(
                    success=False,
                    error="DAX query timed out. The query may be too complex or the dataset too large.",
                )

            except Exception as e:
                error_msg = str(e)
                if is_authentication_error(0, error_msg):
                    return ExecutionResult(
                        success=False,
                        error="Your authentication session has expired. Please refresh the page and try again to re-authenticate.",
                        requires_reauth=True,
                    )
                return ExecutionResult(success=False, error=error_msg)

        return ExecutionResult(success=False, error="Max retries exhausted")

    def _parse_response(self, result_json: dict) -> ExecutionResult:
        """
        Parse the Power BI REST API executeQueries response.

        Response format:
        {
            "results": [{
                "tables": [{
                    "rows": [
                        {"[Col1]": val1, "[Col2]": val2},
                        ...
                    ]
                }]
            }]
        }

        Column names come from the row keys and may have brackets like
        [DimDate].[Year] — we clean them for consistency.
        """
        try:
            results = result_json.get("results", [])
            if not results:
                return ExecutionResult(
                    success=True, columns=[], data=[], row_count=0
                )

            tables = results[0].get("tables", [])
            if not tables:
                return ExecutionResult(
                    success=True, columns=[], data=[], row_count=0
                )

            rows = tables[0].get("rows", [])
            if not rows:
                return ExecutionResult(
                    success=True, columns=[], data=[], row_count=0
                )

            # Extract column names from first row's keys
            raw_columns = list(rows[0].keys())
            # Clean column names: "[Table].[Column]" -> "Column"
            columns = [self._clean_column_name(col) for col in raw_columns]

            # Convert rows (dicts) to lists of values
            data = []
            for row in rows:
                data.append([row.get(col) for col in raw_columns])

            return ExecutionResult(
                success=True,
                columns=columns,
                data=data,
                row_count=len(data),
            )

        except Exception as e:
            return ExecutionResult(
                success=False,
                error=f"Failed to parse Power BI response: {str(e)}",
            )

    @staticmethod
    def _clean_column_name(name: str) -> str:
        """
        Clean Power BI REST API column names.

        Examples:
            "[DimDate].[Year]" -> "Year"
            "[_Measures].[Total Transactions]" -> "Total Transactions"
            "Year" -> "Year"  (passthrough)
        """
        # If it contains brackets, extract the last [part]
        if "[" in name:
            parts = name.split("[")
            last_part = parts[-1].rstrip("]")
            return last_part
        return name

    def close(self) -> None:
        """Close the persistent HTTP client and release connections."""
        if self._client:
            self._client.close()
            print("[REST API] HTTP client closed")

    def disconnect(self) -> None:
        """Alias for close() — kept for interface compatibility."""
        self.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False


# ============================================================================
# SINGLETON EXECUTOR AND PUBLIC API
# ============================================================================

_global_executor: Optional[DAXExecutor] = None


def get_executor(force_new: bool = False) -> DAXExecutor:
    """Get or create the global DAX executor."""
    global _global_executor
    if force_new and _global_executor is not None:
        _global_executor = None
    if _global_executor is None:
        _global_executor = DAXExecutor()
    return _global_executor


def reset_executor() -> None:
    """Reset the global executor."""
    global _global_executor
    _global_executor = None


def execute_dax(dax_query: str) -> ExecutionResult:
    """
    Execute a DAX query against Power BI.

    This is the main entry point for the tool.

    Args:
        dax_query: The DAX EVALUATE statement to execute

    Returns:
        ExecutionResult with columns, data, and row count or error

    Example:
        >>> result = execute_dax("EVALUATE SUMMARIZE(DimDate, DimDate[Year])")
        >>> if result.success:
        ...     print(f"Got {result.row_count} rows")
    """
    executor = get_executor()
    return executor.execute(dax_query)


def cleanup_executor() -> None:
    """Clean up the global executor (no-op for REST API, kept for compatibility)."""
    global _global_executor
    _global_executor = None


# ============================================================================
# MAIN — Test DAX query execution
# ============================================================================

if __name__ == "__main__":
    import argparse

    DEFAULT_QUERY = '''EVALUATE ROW(
        "Total Transactions",
        CALCULATE(
            [Total Transactions],
            FILTER(ALL('DimServiceUni'), 'DimServiceUni'[Service_Name] = "Pay Traffic Fines")
        )
    )'''

    parser = argparse.ArgumentParser(description="Execute DAX query against Power BI (REST API)")
    parser.add_argument("--query", "-q", type=str, default=DEFAULT_QUERY,
                        help="DAX query to execute")
    parser.add_argument("--file", "-f", type=str, default=None,
                        help="Read DAX query from file")
    parser.add_argument("--token", "-t", type=str, default=None,
                        help="Power BI access token (or set via env var POWERBI_TOKEN)")
    args = parser.parse_args()

    if args.file:
        with open(args.file, 'r', encoding='utf-8') as f:
            dax_query = f.read().strip()
    else:
        dax_query = args.query

    token = args.token or os.getenv("POWERBI_TOKEN")
    if not token:
        print("[ERROR] Provide a Power BI access token via --token or POWERBI_TOKEN env var")
        sys.exit(1)

    print("=" * 60)
    print("DAX Query Executor (Power BI REST API)")
    print("=" * 60)
    print()
    print("Query:")
    print(dax_query)
    print()
    print("-" * 60)
    print("Executing...")
    print()

    try:
        executor = get_executor()
        executor.set_access_token(token)
        result = executor.execute(dax_query)

        if result.success:
            print(f"[OK] Query executed successfully!")
            print(f"Rows returned: {result.row_count}")
            print()
            print("Columns:", result.columns)
            print()

            if result.data:
                print("Results:")
                print("-" * 40)
                header = " | ".join(str(col) for col in result.columns)
                print(header)
                print("-" * len(header))
                for row in result.data[:20]:
                    print(" | ".join(str(val) for val in row))

                if result.row_count > 20:
                    print(f"... ({result.row_count - 20} more rows)")
            else:
                print("No data returned.")
        else:
            print(f"[ERROR] Query failed: {result.error}")

    except Exception as e:
        print(f"[ERROR] Exception: {e}")

    finally:
        cleanup_executor()
        print()
        print("=" * 60)
        print("Done.")