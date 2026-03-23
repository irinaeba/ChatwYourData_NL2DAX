# backend/tools/__init__.py
"""
DAX Agent Tools

This package contains tools for the DAX generation workflow:
- generate_dax: Generate DAX queries using Azure OpenAI
- dax_validator: Validate and improve generated DAX queries
- execute_dax: Execute DAX queries against Power BI
- format_dax_results: Format execution results into human-readable text
- chart_visualizer: Create Chart.js visualizations from DAX results
- query_planner: LLM-based query planner for domain routing

Schema files used:
- Transactions: cache/schema/schema_transactions.txt
- Feedback: cache/schema/schema_feedback.txt
"""

import sys
from pathlib import Path

# Add project root to path BEFORE importing modules that need tools.auth
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from .generate_dax import generate_dax, DAXGenerator, ConversationTurn
from .validate_dax import DAXValidator, ValidationResult
from .execute_dax import execute_dax, DAXExecutor, ExecutionResult, get_executor, cleanup_executor
from .format_dax_results import format_dax_results, DAXResultsFormatter, FormattedResult, get_formatter, cleanup_formatter
from .chart_visualizer import create_chart_visualization, ChartVisualizer, ChartConfig, VisualizationResult, get_visualizer
from .query_planner import get_planner, QueryPlanner, ExecutionPlan, PlanStep

__all__ = [
    'generate_dax',
    'DAXGenerator',
    'ConversationTurn',
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
    'get_planner',
    'QueryPlanner',
    'ExecutionPlan',
    'PlanStep',
]
