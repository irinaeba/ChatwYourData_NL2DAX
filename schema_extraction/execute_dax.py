"""
Power BI XMLA Client for DAX Query Execution

Uses pyadomd (ADOMD.NET via pythonnet) to execute DAX queries and DMV
statements against Power BI datasets through the XMLA endpoint.

The XMLA approach supports both delegated and **service principal** auth,
unlike the Execute Queries REST API which only accepts delegated tokens.

Prerequisites:
  - XMLA read must be enabled in the tenant (Admin Portal > Integration settings)
  - The caller (SP or user) needs workspace Member / Admin / Contributor role
  - pyadomd + pythonnet installed:  pip install pyadomd
  - ADOMD.NET DLL in lib/net45/ (from NuGet: Microsoft.AnalysisServices.AdomdClient.retail.amd64)

Usage:
    from execute_dax import PowerBIXmlaClient

    client = PowerBIXmlaClient(workspace_name, database_name,
                                client_id=..., tenant_id=..., client_secret=...)
    rows = client.execute_dax("EVALUATE INFO.VIEW.TABLES()")
"""

import os
import sys
from typing import Any, Dict, List, Optional
from pathlib import Path


# ============================================================
# Ensure ADOMD.NET DLL is discoverable by pythonnet
# ============================================================

def _setup_adomd_dll():
    """Add the ADOMD.NET DLL directory to sys.path so pyadomd can find it."""
    # Try lib/net45 relative to this file's project root
    dll_dir = Path(__file__).resolve().parent.parent / "lib" / "net45"
    if dll_dir.is_dir() and str(dll_dir) not in sys.path:
        sys.path.insert(0, str(dll_dir))
        return str(dll_dir)
    return None

_setup_adomd_dll()


# ============================================================
# Power BI XMLA Client
# ============================================================

class PowerBIXmlaClient:
    """
    Power BI XMLA client for executing DAX queries via ADOMD.NET.

    Connects to the powerbi:// protocol endpoint using pyadomd/pythonnet.
    Supports service principal auth (app:client_id@tenant_id + secret)
    and pre-acquired bearer tokens.

    Each query opens a fresh connection to avoid XmlReader conflicts.
    """

    def __init__(
        self,
        workspace_name: str,
        database_name: str,
        client_id: str = None,
        tenant_id: str = None,
        client_secret: str = None,
        access_token: str = None,
    ):
        """
        Initialize the XMLA client.

        Provide EITHER (client_id + tenant_id + client_secret) for service
        principal auth, OR access_token for pre-acquired token auth.

        Args:
            workspace_name: Power BI workspace name (not GUID).
            database_name: Semantic model / database name.
            client_id: Service principal app (client) ID.
            tenant_id: Azure AD tenant ID.
            client_secret: Service principal client secret.
            access_token: Pre-acquired bearer token (alternative to SP creds).
        """
        self.workspace_name = workspace_name
        self.database_name = database_name

        data_source = f"powerbi://api.powerbi.com/v1.0/myorg/{workspace_name}"

        if access_token:
            # Token-based auth — pass token as Password
            self._conn_str = (
                f"Provider=MSOLAP;"
                f"Data Source={data_source};"
                f"Initial Catalog={database_name};"
                f"Password={access_token};"
                f"Persist Security Info=True;"
                f"Impersonation Level=Impersonate"
            )
        elif client_id and tenant_id and client_secret:
            # Service principal auth — ADOMD.NET handles the OAuth flow
            self._conn_str = (
                f"Provider=MSOLAP;"
                f"Data Source={data_source};"
                f"Initial Catalog={database_name};"
                f"User ID=app:{client_id}@{tenant_id};"
                f"Password={client_secret};"
                f"Persist Security Info=True;"
                f"Impersonation Level=Impersonate"
            )
        else:
            raise ValueError(
                "Provide either (client_id + tenant_id + client_secret) "
                "or access_token"
            )

    def execute_dax(self, query: str) -> List[Dict[str, Any]]:
        """
        Execute a DAX query and return rows as list of dicts.

        Opens a fresh connection per query, executes the statement,
        reads all rows, then closes cleanly. This avoids the ADOMD.NET
        XmlReader conflict that occurs when reusing a connection.

        Args:
            query: DAX EVALUATE statement or DMV SELECT statement.

        Returns:
            List of row dictionaries, with bracket-stripped column names.

        Raises:
            RuntimeError: If the query or connection fails.
        """
        from pyadomd import Pyadomd

        try:
            conn = Pyadomd(self._conn_str)
            conn.open()
        except Exception as e:
            raise RuntimeError(f"XMLA connection failed: {e}") from e

        try:
            cursor = conn.cursor()
            result = cursor.execute(query)

            # Get column names from the description
            col_names = [self._clean_col_name(d.name) for d in result.description]

            # Read all rows
            rows = []
            for row_tuple in result.fetchone():
                row_dict = {}
                for i, val in enumerate(row_tuple):
                    row_dict[col_names[i]] = val
                rows.append(row_dict)

            cursor.close()
            return rows
        except Exception as e:
            raise RuntimeError(f"DAX query failed: {e}") from e
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _clean_col_name(name: str) -> str:
        """
        Clean column names returned by ADOMD.NET.

        ADOMD.NET returns column names like "[ID]" or "Table[Name]".
        Extract just the bracketed column name.
        """
        if "[" in name:
            start = name.rfind("[")
            end = name.rfind("]")
            if start != -1 and end != -1 and end > start:
                return name[start + 1 : end]
        return name


# ============================================================
# Backward compatibility alias
# ============================================================
# The old REST client signature was:
#   PowerBIRestClient(workspace_id, dataset_id, access_token)
# The new XMLA client uses different params. If migrating existing code,
# use PowerBIXmlaClient directly with named parameters.
