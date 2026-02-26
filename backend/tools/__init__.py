# backend/tools/__init__.py
"""
DAX Agent Tools

This package contains tools for the DAX generation workflow:
- read_schema: Read full schema from cached file (schema_tamm.txt)
- extract_intent: Detect query intent and load domain-specific schema
- generate_dax: Generate DAX queries using Azure OpenAI
- dax_validator: Validate and improve generated DAX queries
- execute_dax: Execute DAX queries against Power BI
- format_dax_results: Format execution results into human-readable text
- chart_visualizer: Create Chart.js visualizations from DAX results

Schema files used:
- Transactions: cache/schema/schema_transactions.txt
- Feedback: cache/schema/schema_feedback.txt  
- Full: cache/schema/schema_tamm.txt
"""

import sys
from pathlib import Path

# Add project root to path BEFORE importing modules that need tools.auth
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# from .read_schema import read_schema, SchemaReader
from .extract_intent import extract_intent, IntentExtractor, QueryIntent, IntentResult
from .generate_dax import generate_dax, DAXGenerator, ConversationTurn
from .validate_dax import DAXValidator, ValidationResult
from .execute_dax import execute_dax, DAXExecutor, ExecutionResult, get_executor, cleanup_executor
from .format_dax_results import format_dax_results, DAXResultsFormatter, FormattedResult, get_formatter, cleanup_formatter
from .chart_visualizer import create_chart_visualization, ChartVisualizer, ChartConfig, VisualizationResult, get_visualizer

__all__ = [
   # 'read_schema',
   # 'SchemaReader',
    'extract_intent',
    'IntentExtractor',
    'QueryIntent',
    'IntentResult',
    'generate_dax',
    'DAXGenerator',
    'ConversationTurn',
    'validate_dax',
    'DAXValidator',
    'ValidationResult',
    'execute_dax',
    'DAXExecutor',
    'ExecutionResult',
    'get_executor',
    'cleanup_executor',
    'format_dax_results',
    'DAXResultsFormatter',
    'FormattedResult',
    'get_formatter',
    'cleanup_formatter',
    'create_chart_visualization',
    'ChartVisualizer',
    'ChartConfig',
    'VisualizationResult',
    'get_visualizer',
]
