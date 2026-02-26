"""
DAX Query Execution Module

This module handles execution of DAX queries against Power BI datasets
using XMLA endpoints via ADOMD.NET.
"""

import os
import clr
from typing import List, Dict, Any, Optional


class AdomdClientNotLoadedError(Exception):
    """Raised when ADOMD.NET client fails to load."""
    pass


class DaxQueryExecutor:
    """
    Executes DAX queries against Power BI datasets using XMLA endpoints.
    
    This class manages:
    - ADOMD.NET DLL loading and initialization
    - XMLA connection setup and management
    - DAX query execution and result processing
    """
    
    def __init__(self, adomd_dll_path: str, workspace_name: str, 
                 database_name: str, access_token: str):
        """
        Initialize the DAX Query Executor.
        
        Args:
            adomd_dll_path (str): Path to Microsoft.AnalysisServices.AdomdClient.dll
            workspace_name (str): Power BI workspace name
            database_name (str): Power BI dataset/database name
            access_token (str): Azure AD access token for authentication
        
        Raises:
            RuntimeError: If ADOMD DLL not found
            AdomdClientNotLoadedError: If ADOMD.NET fails to load
        """
        self.adomd_dll_path = adomd_dll_path
        self.workspace_name = workspace_name
        self.database_name = database_name
        self.access_token = access_token
        
        self.connection = None
        self._adomd_client = None
        
        # Load and validate ADOMD DLL
        self._load_adomd_client()
        self._build_connection_string()
    
    def _load_adomd_client(self):
        """
        Load ADOMD.NET client from DLL.
        
        Raises:
            RuntimeError: If DLL not found
            AdomdClientNotLoadedError: If DLL fails to load
        """
        dll_path = os.path.abspath(self.adomd_dll_path)
        
        if not os.path.exists(dll_path):
            raise RuntimeError(
                f"ADOMD DLL not found at {dll_path}.\n"
                "Please download and place the Microsoft.AnalysisServices.AdomdClient.dll "
                "into that path or set ADOMD_DLL in your .env to the correct path.\n"
                "On Windows, install the ADOMD.NET redistributable or copy the DLL from the appropriate SDK."
            )
        
        try:
            clr.AddReference(dll_path)
            from Microsoft.AnalysisServices.AdomdClient import AdomdConnection  # type: ignore
            self._adomd_client = AdomdConnection
        except Exception as e:
            raise AdomdClientNotLoadedError(
                f"Failed to load ADOMD.NET client from {dll_path}: {str(e)}"
            )
    
    def _build_connection_string(self):
        """Build XMLA connection string."""
        xmla_endpoint = f"powerbi://api.powerbi.com/v1.0/myorg/{self.workspace_name}"
        
        self.conn_str = (
            "Provider=MSOLAP;"
            f"Data Source={xmla_endpoint};"
            f"Initial Catalog={self.database_name};"
            "Integrated Security=ClaimsToken;"
            "User ID=;"
            f"Password={self.access_token};"
        )
    
    def connect(self):
        """
        Establish connection to the XMLA endpoint.
        
        Raises:
            RuntimeError: If connection fails
        """
        try:
            print("Connecting to XMLA...")
            self.connection = self._adomd_client(self.conn_str)
            self.connection.Open()
            print("[OK] XMLA connection established")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to XMLA: {str(e)}")
    
    def disconnect(self):
        """Close the connection."""
        if self.connection:
            try:
                self.connection.Close()
                print("[OK] XMLA connection closed")
            except Exception as e:
                print(f"Warning: Error closing connection: {e}")
            finally:
                self.connection = None
    
    def execute(self, dax_query: str) -> List[List[Any]]:
        """
        Execute a DAX query and return results.
        
        Args:
            dax_query (str): DAX query string to execute
        
        Returns:
            List[List[Any]]: Query results as list of rows (each row is a list of values)
        
        Raises:
            RuntimeError: If connection not established or query fails
        """
        if not self.connection:
            raise RuntimeError("Not connected to XMLA. Call connect() first.")
        
        try:
            cmd = self.connection.CreateCommand()
            cmd.CommandText = dax_query
            
            reader = cmd.ExecuteReader()
            results = []
            
            while reader.Read():
                row = [reader.GetValue(i) for i in range(reader.FieldCount)]
                results.append(row)
            
            reader.Close()
            return results
        except Exception as e:
            raise RuntimeError(f"DAX query execution failed: {str(e)}")
    
    def execute_with_columns(self, dax_query: str) -> Dict[str, List[Any]]:
        """
        Execute a DAX query and return results with column names.
        
        Args:
            dax_query (str): DAX query string to execute
        
        Returns:
            Dict[str, List[Any]]: Dictionary with column names as keys 
                                 and lists of values as values
        
        Raises:
            RuntimeError: If connection not established or query fails
        """
        if not self.connection:
            raise RuntimeError("Not connected to XMLA. Call connect() first.")
        
        try:
            cmd = self.connection.CreateCommand()
            cmd.CommandText = dax_query
            
            reader = cmd.ExecuteReader()
            
            # Get column names
            columns = [reader.GetName(i) for i in range(reader.FieldCount)]
            results = {col: [] for col in columns}
            
            # Read data
            while reader.Read():
                for i, col in enumerate(columns):
                    results[col].append(reader.GetValue(i))
            
            reader.Close()
            return results
        except Exception as e:
            raise RuntimeError(f"DAX query execution failed: {str(e)}")
    
    def execute_with_metadata(self, dax_query: str) -> Dict[str, Any]:
        """
        Execute a DAX query and return results with column metadata.
        
        Args:
            dax_query (str): DAX query string to execute
        
        Returns:
            Dict with keys:
                - 'columns': List of column names
                - 'data': List of rows (each row is a list of values)
                - 'row_count': Number of rows returned
        
        Raises:
            RuntimeError: If connection not established or query fails
        """
        if not self.connection:
            raise RuntimeError("Not connected to XMLA. Call connect() first.")
        
        try:
            cmd = self.connection.CreateCommand()
            cmd.CommandText = dax_query
            
            reader = cmd.ExecuteReader()
            
            # Get column names
            columns = [reader.GetName(i) for i in range(reader.FieldCount)]
            data = []
            
            # Read data
            while reader.Read():
                row = [reader.GetValue(i) for i in range(reader.FieldCount)]
                data.append(row)
            
            reader.Close()
            
            return {
                'columns': columns,
                'data': data,
                'row_count': len(data)
            }
        except Exception as e:
            raise RuntimeError(f"DAX query execution failed: {str(e)}")
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
        return False
