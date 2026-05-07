"""
Chart Visualizer Tool

Creates Chart.js visualizations from DAX query results.
Uses chart metadata from DAX validation to determine appropriate chart type.

Rules:
1. If dimension_type is 'date' → line chart ordered by date dimension
2. If dimension_type is 'categorical' → horizontal bar chart
3. If dimension_type is 'none' or no dimension → no chart generated
"""

import json
import re
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime


@dataclass
class ChartMetadata:
    """Metadata for chart generation passed from DAX validation."""
    metric_name: Optional[str] = None
    dimension: Optional[str] = None
    dimension_type: str = "none"  # 'date', 'categorical', or 'none'


@dataclass
class ChartConfig:
    """Chart.js configuration object."""
    chart_type: str  # 'bar', 'line', 'horizontalBar', 'pie', 'none'
    labels: List[str] = field(default_factory=list)
    datasets: List[Dict[str, Any]] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)
    title: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to Chart.js compatible dictionary."""
        if self.chart_type == "none":
            return {"type": "none", "reason": "Single value result - no chart needed"}
        
        return {
            "type": self.chart_type,
            "data": {
                "labels": self.labels,
                "datasets": self.datasets,
            },
            "options": self.options,
        }
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str, indent=2)


@dataclass
class VisualizationResult:
    """Result containing both chart and formatted response."""
    success: bool
    chart_config: Optional[ChartConfig] = None
    formatted_response: Optional[str] = None
    chart_type: str = "none"
    skip_reason: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "success": self.success,
            "chart_type": self.chart_type,
            "formatted_response": self.formatted_response,
        }
        
        if self.chart_config:
            result["chart_config"] = self.chart_config.to_dict()
        
        if self.skip_reason:
            result["skip_reason"] = self.skip_reason
        
        if self.error:
            result["error"] = self.error
            
        return result
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str, indent=2)


class ChartVisualizer:
    """
    Creates appropriate Chart.js visualizations from DAX query results.
    
    Analyzes the data structure and content to determine the best chart type:
    - Single value: No chart
    - Time series: Column/bar chart ordered by date
    - Categorical: Horizontal bar chart with top 10
    """
    
    # Default color palette for charts
    COLORS = [
        "rgba(54, 162, 235, 0.8)",   # Blue
        "rgba(255, 99, 132, 0.8)",   # Red
        "rgba(75, 192, 192, 0.8)",   # Teal
        "rgba(255, 206, 86, 0.8)",   # Yellow
        "rgba(153, 102, 255, 0.8)", # Purple
        "rgba(255, 159, 64, 0.8)",  # Orange
        "rgba(199, 199, 199, 0.8)", # Gray
        "rgba(83, 102, 255, 0.8)",  # Indigo
        "rgba(255, 99, 255, 0.8)",  # Pink
        "rgba(99, 255, 132, 0.8)",  # Green
    ]
    
    BORDER_COLORS = [
        "rgba(54, 162, 235, 1)",
        "rgba(255, 99, 132, 1)",
        "rgba(75, 192, 192, 1)",
        "rgba(255, 206, 86, 1)",
        "rgba(153, 102, 255, 1)",
        "rgba(255, 159, 64, 1)",
        "rgba(199, 199, 199, 1)",
        "rgba(83, 102, 255, 1)",
        "rgba(255, 99, 255, 1)",
        "rgba(99, 255, 132, 1)",
    ]
    
    # Date-related column name patterns
    DATE_PATTERNS = [
        r'date', r'time', r'year', r'month', r'quarter', r'week', r'day',
        r'period', r'fiscal', r'calendar', r'datetime', r'timestamp'
    ]
    
    # Numeric/measure patterns (for identifying value columns)
    MEASURE_PATTERNS = [
        r'sum', r'avg', r'average', r'count', r'total', r'amount', r'value',
        r'revenue', r'cost', r'price', r'quantity', r'qty', r'sales',
        r'profit', r'margin', r'score', r'rate', r'percent', r'ratio'
    ]
    
    def __init__(self):
        """Initialize the chart visualizer."""
        pass
    
    def create_visualization(
        self,
        columns: List[str],
        data: List[List[Any]],
        user_query: str = "",
        formatted_response: str = "",
        chart_metadata: Optional[ChartMetadata] = None,
    ) -> VisualizationResult:
        """
        Create a chart visualization from DAX results.
        
        Args:
            columns: List of column names
            data: List of rows (each row is a list of values)
            user_query: Original user question (for context)
            formatted_response: Pre-formatted text response to include
            chart_metadata: Metadata from DAX validation (metric_name, dimension, dimension_type)
            
        Returns:
            VisualizationResult with chart config and formatted response
        """
        try:
            # Rule 3: No dimension passed → no chart
            if chart_metadata is None or chart_metadata.dimension_type == "none":
                return VisualizationResult(
                    success=True,
                    chart_type="none",
                    formatted_response=formatted_response,
                    skip_reason="No dimension for chart - single aggregated value",
                )
            
            # Also skip if no data
            if self._is_single_value(columns, data):
                return VisualizationResult(
                    success=True,
                    chart_type="none",
                    formatted_response=formatted_response,
                    skip_reason="Single value result - no chart needed",
                )
            
            # Rule 1: Date dimension → line chart
            if chart_metadata.dimension_type == "date":
                chart_config = self._create_line_chart(columns, data, chart_metadata)
                return VisualizationResult(
                    success=True,
                    chart_config=chart_config,
                    chart_type="line",
                    formatted_response=formatted_response,
                )
            
            # Rule 2: Categorical dimension → horizontal bar chart
            if chart_metadata.dimension_type == "categorical":
                chart_config = self._create_horizontal_bar_chart(columns, data, chart_metadata)
                return VisualizationResult(
                    success=True,
                    chart_config=chart_config,
                    chart_type="bar",
                    formatted_response=formatted_response,
                )
            
            # Fallback: no chart
            return VisualizationResult(
                success=True,
                chart_type="none",
                formatted_response=formatted_response,
                skip_reason=f"Unknown dimension type: {chart_metadata.dimension_type}",
            )
            
        except Exception as e:
            return VisualizationResult(
                success=False,
                formatted_response=formatted_response,
                error=f"Chart creation failed: {str(e)}",
            )
    
    def _is_single_value(self, columns: List[str], data: List[List[Any]]) -> bool:
        """Check if result is a single value."""
        if len(data) == 0:
            return True
        if len(data) == 1 and len(columns) == 1:
            return True
        return False
    
    def _classify_data(self, columns: List[str], data: List[List[Any]]) -> str:
        """
        Classify the data type for chart selection.
        
        Returns:
            'time_series' or 'categorical'
        """
        # Check for date columns
        date_col_idx = self._find_date_column(columns, data)
        
        if date_col_idx is not None:
            return "time_series"
        
        return "categorical"
    
    def _find_date_column(self, columns: List[str], data: List[List[Any]]) -> Optional[int]:
        """
        Find the index of a date/time column.
        
        Returns:
            Column index or None if no date column found
        """
        for idx, col in enumerate(columns):
            col_lower = col.lower()
            
            # Check column name patterns
            for pattern in self.DATE_PATTERNS:
                if re.search(pattern, col_lower, re.IGNORECASE):
                    return idx
            
            # Check if values look like dates
            if data and len(data) > 0 and idx < len(data[0]):
                if self._looks_like_date(data[0][idx]):
                    return idx
        
        return None
    
    def _looks_like_date(self, value: Any) -> bool:
        """Check if a value looks like a date."""
        if value is None:
            return False
        
        if isinstance(value, datetime):
            return True
        
        str_val = str(value)
        
        # Check for year-like 4-digit numbers
        if re.match(r'^(19|20)\d{2}$', str_val):
            return True
        
        # Check for date formats
        date_formats = [
            r'\d{4}-\d{2}-\d{2}',  # 2024-01-15
            r'\d{2}/\d{2}/\d{4}',  # 01/15/2024
            r'\d{2}-\d{2}-\d{4}',  # 15-01-2024
            r'Q[1-4]\s*\d{4}',     # Q1 2024
            r'\w+\s+\d{4}',        # January 2024
        ]
        
        for fmt in date_formats:
            if re.match(fmt, str_val):
                return True
        
        return False
    
    def _find_measure_column(self, columns: List[str], exclude_idx: int = -1) -> int:
        """
        Find the index of a numeric/measure column.
        
        Args:
            columns: List of column names
            exclude_idx: Column index to exclude (e.g., the category column)
            
        Returns:
            Column index of the measure, defaults to last column
        """
        for idx, col in enumerate(columns):
            if idx == exclude_idx:
                continue
            
            col_lower = col.lower()
            for pattern in self.MEASURE_PATTERNS:
                if re.search(pattern, col_lower, re.IGNORECASE):
                    return idx
        
        # Default to last numeric column that's not the excluded one
        for idx in range(len(columns) - 1, -1, -1):
            if idx != exclude_idx:
                return idx
        
        return len(columns) - 1
    
    def _create_time_series_chart(
        self,
        columns: List[str],
        data: List[List[Any]],
        user_query: str = "",
    ) -> ChartConfig:
        """
        Create a column chart for time series data, ordered by date.
        DEPRECATED: Use _create_line_chart with chart_metadata instead.
        """
        date_col_idx = self._find_date_column(columns, data)
        measure_col_idx = self._find_measure_column(columns, exclude_idx=date_col_idx)
        
        # Sort data by date
        sorted_data = sorted(data, key=lambda row: str(row[date_col_idx]) if date_col_idx < len(row) else "")
        
        # Extract labels and values
        labels = [str(row[date_col_idx]) for row in sorted_data if date_col_idx < len(row)]
        values = [row[measure_col_idx] if measure_col_idx < len(row) else 0 for row in sorted_data]
        
        # Convert values to numbers
        values = [float(v) if v is not None else 0 for v in values]
        
        measure_name = columns[measure_col_idx] if measure_col_idx < len(columns) else "Value"
        
        return ChartConfig(
            chart_type="bar",
            labels=labels,
            datasets=[{
                "label": measure_name,
                "data": values,
                "backgroundColor": self.COLORS[0],
                "borderColor": self.BORDER_COLORS[0],
                "borderWidth": 1,
            }],
            options={
                "responsive": True,
                "plugins": {
                    "title": {
                        "display": True,
                        "text": f"{measure_name} Over Time",
                    },
                    "legend": {
                        "display": True,
                        "position": "top",
                    },
                },
                "scales": {
                    "x": {
                        "title": {
                            "display": True,
                            "text": columns[date_col_idx] if date_col_idx < len(columns) else "Date",
                        },
                    },
                    "y": {
                        "title": {
                            "display": True,
                            "text": measure_name,
                        },
                        "beginAtZero": True,
                    },
                },
            },
            title=f"{measure_name} Over Time",
        )
    
    def _create_line_chart(
        self,
        columns: List[str],
        data: List[List[Any]],
        chart_metadata: ChartMetadata,
    ) -> ChartConfig:
        """
        Create a line chart for date-based dimensions.
        Uses chart_metadata to identify the dimension and metric.
        """
        # Find dimension column (date)
        dimension_col_idx = self._find_column_by_name(columns, chart_metadata.dimension)
        if dimension_col_idx is None:
            dimension_col_idx = self._find_date_column(columns, data) or 0
        
        # Find measure column
        measure_col_idx = self._find_column_by_name(columns, chart_metadata.metric_name)
        if measure_col_idx is None:
            measure_col_idx = self._find_measure_column(columns, exclude_idx=dimension_col_idx)
        
        # Detect year + month columns for combined labels
        year_col_idx = None
        month_col_idx = None
        month_name_col_idx = None
        cols_lower = [c.lower() for c in columns]
        
        for idx, cl in enumerate(cols_lower):
            if cl in ('year',) or cl.endswith('[year]'):
                year_col_idx = idx
            elif cl in ('month',) or cl.endswith('[month]'):
                month_col_idx = idx
            elif 'month_name' in cl or 'monthname' in cl or cl in ('month_name',):
                month_name_col_idx = idx
        
        use_year_month = year_col_idx is not None and (month_col_idx is not None or month_name_col_idx is not None)
        
        # Sort data by year+month if available, otherwise by dimension
        if use_year_month:
            sort_key_idx = month_col_idx if month_col_idx is not None else month_name_col_idx
            sorted_data = sorted(data, key=lambda row: (
                int(row[year_col_idx]) if year_col_idx < len(row) and row[year_col_idx] is not None else 0,
                int(row[sort_key_idx]) if sort_key_idx < len(row) and row[sort_key_idx] is not None and str(row[sort_key_idx]).isdigit() else str(row[sort_key_idx]) if sort_key_idx < len(row) else ""
            ))
        else:
            sorted_data = sorted(data, key=lambda row: str(row[dimension_col_idx]) if dimension_col_idx < len(row) else "")
        
        # Build labels: prefer "MonthName Year" format when year+month columns exist
        if use_year_month:
            labels = []
            for row in sorted_data:
                year_val = str(row[year_col_idx]) if year_col_idx < len(row) else ""
                if month_name_col_idx is not None and month_name_col_idx < len(row) and row[month_name_col_idx]:
                    month_val = str(row[month_name_col_idx])
                    # Shorten month name to 3 chars if longer
                    if len(month_val) > 3:
                        month_val = month_val[:3]
                    labels.append(f"{month_val} {year_val}")
                elif month_col_idx is not None and month_col_idx < len(row):
                    labels.append(f"{int(row[month_col_idx]):02d}/{year_val}")
                else:
                    labels.append(year_val)
            dimension_name = "Month"
        else:
            labels = [str(row[dimension_col_idx]) for row in sorted_data if dimension_col_idx < len(row)]
            dimension_name = self._clean_column_name(chart_metadata.dimension or columns[dimension_col_idx])
        
        values = [self._to_number(row[measure_col_idx]) if measure_col_idx < len(row) else 0 for row in sorted_data]
        
        metric_name = chart_metadata.metric_name or columns[measure_col_idx] if measure_col_idx < len(columns) else "Value"
        
        # Determine if we should show datalabels (only for 7 or fewer values)
        show_datalabels = len(values) <= 7
        
        return ChartConfig(
            chart_type="line",
            labels=labels,
            datasets=[{
                "label": metric_name,
                "data": values,
                "borderColor": self.BORDER_COLORS[0],
                "backgroundColor": self.COLORS[0],
                "borderWidth": 2,
                "fill": False,
                "tension": 0.1,  # Slight curve for smoother lines
                "pointRadius": 4,
                "pointHoverRadius": 6,
            }],
            options={
                "responsive": True,
                "plugins": {
                    "title": {
                        "display": True,
                        "text": f"{metric_name} by {dimension_name}",
                    },
                    "legend": {
                        "display": True,
                        "position": "top",
                    },
                    "datalabels": {
                        "display": show_datalabels,
                        "anchor": "end",
                        "align": "top",
                        "offset": 8,  # Lift labels higher above the line
                        "color": "#333",
                        "font": {
                            "weight": "bold",
                            "size": 11,
                        },
                        "formatter": "__DATALABEL_FORMATTER__",
                    },
                },
                "scales": {
                    "x": {
                        "title": {
                            "display": True,
                            "text": dimension_name,
                        },
                    },
                    "y": {
                        "title": {
                            "display": True,
                            "text": metric_name,
                        },
                        "beginAtZero": True,
                    },
                },
            },
            title=f"{metric_name} by {dimension_name}",
        )
    
    def _create_horizontal_bar_chart(
        self,
        columns: List[str],
        data: List[List[Any]],
        chart_metadata: ChartMetadata,
    ) -> ChartConfig:
        """
        Create a horizontal bar chart for categorical dimensions.
        Uses chart_metadata to identify the dimension and metric.
        Shows top 10 values in descending order by measure.
        """
        # Find dimension column (category)
        category_col_idx = self._find_column_by_name(columns, chart_metadata.dimension)
        if category_col_idx is None:
            category_col_idx = 0
        
        # Find measure column
        measure_col_idx = self._find_column_by_name(columns, chart_metadata.metric_name)
        if measure_col_idx is None:
            measure_col_idx = self._find_measure_column(columns, exclude_idx=category_col_idx)
        
        # Convert measure values to numbers for sorting
        data_with_values = []
        for row in data:
            if len(row) > measure_col_idx:
                value = self._to_number(row[measure_col_idx])
                data_with_values.append((row, value))
            else:
                data_with_values.append((row, 0))
        
        # Sort by measure descending and take top 10
        sorted_data = sorted(data_with_values, key=lambda x: x[1], reverse=True)[:10]
        
        # Extract labels and values
        labels = [str(row[0][category_col_idx]) if category_col_idx < len(row[0]) else "" for row in sorted_data]
        values = [row[1] for row in sorted_data]
        
        metric_name = chart_metadata.metric_name or columns[measure_col_idx] if measure_col_idx < len(columns) else "Value"
        category_name = self._clean_column_name(chart_metadata.dimension or columns[category_col_idx])
        
        # Use different colors for each bar
        bg_colors = [self.COLORS[i % len(self.COLORS)] for i in range(len(values))]
        border_colors = [self.BORDER_COLORS[i % len(self.BORDER_COLORS)] for i in range(len(values))]
        
        # Determine if we should show datalabels (only for 7 or fewer values)
        show_datalabels = len(values) <= 7
        
        return ChartConfig(
            chart_type="bar",
            labels=labels,
            datasets=[{
                "label": metric_name,
                "data": values,
                "backgroundColor": bg_colors,
                "borderColor": border_colors,
                "borderWidth": 1,
            }],
            options={
                "indexAxis": "y",  # This makes it a horizontal bar chart
                "responsive": True,
                "plugins": {
                    "title": {
                        "display": True,
                        "text": f"Top 10 {category_name} by {metric_name}",
                    },
                    "legend": {
                        "display": False,
                    },
                    "datalabels": {
                        "display": show_datalabels,
                        "anchor": "end",
                        "align": "end",
                        "color": "#333",
                        "font": {
                            "weight": "bold",
                            "size": 11,
                        },
                        "formatter": "__DATALABEL_FORMATTER__",  # Will be replaced in frontend
                    },
                },
                "scales": {
                    "x": {
                        "title": {
                            "display": True,
                            "text": metric_name,
                        },
                        "beginAtZero": True,
                    },
                    "y": {
                        "title": {
                            "display": True,
                            "text": category_name,
                        },
                    },
                },
            },
            title=f"Top 10 {category_name} by {metric_name}",
        )
    
    def _find_column_by_name(self, columns: List[str], name: Optional[str]) -> Optional[int]:
        """Find column index by partial name match."""
        if not name:
            return None
        
        name_lower = name.lower()
        # Clean name (remove table prefix like 'DimServiceUni[Service_Name]' -> 'service_name')
        if '[' in name_lower and ']' in name_lower:
            name_lower = name_lower.split('[')[1].replace(']', '')
        
        for idx, col in enumerate(columns):
            col_lower = col.lower()
            # Clean column name too
            if '[' in col_lower and ']' in col_lower:
                col_lower = col_lower.split('[')[1].replace(']', '')
            
            if name_lower in col_lower or col_lower in name_lower:
                return idx
        
        return None
    
    def _clean_column_name(self, name: Optional[str]) -> str:
        """Clean column name for display (remove table prefix)."""
        if not name:
            return "Value"
        
        # Handle format like 'DimServiceUni[Service_Name]' -> 'Service Name'
        if '[' in name and ']' in name:
            name = name.split('[')[1].replace(']', '')
        
        # Convert underscores to spaces and title case
        return name.replace('_', ' ').title()
    
    def _to_number(self, value: Any) -> float:
        """Convert value to number, returning 0 for invalid values.
        
        Handles percentage strings by stripping '%' and converting.
        """
        if value is None:
            return 0
        try:
            # Handle string values that might contain '%'
            if isinstance(value, str):
                value = value.strip()
                if value.endswith('%'):
                    # Remove '%' and convert (value is already as percentage, e.g., '45.3%' -> 45.3)
                    return float(value.rstrip('%').strip())
            return float(value)
        except (ValueError, TypeError):
            return 0
    
    def _create_categorical_chart(
        self,
        columns: List[str],
        data: List[List[Any]],
        user_query: str = "",
    ) -> ChartConfig:
        """
        Create a horizontal bar chart for categorical data.
        Shows top 10 values in descending order by measure.
        """
        # Assume first column is category, find measure column
        category_col_idx = 0
        measure_col_idx = self._find_measure_column(columns, exclude_idx=category_col_idx)
        
        # Convert measure values to numbers for sorting
        data_with_values = []
        for row in data:
            if len(row) > measure_col_idx:
                try:
                    value = float(row[measure_col_idx]) if row[measure_col_idx] is not None else 0
                except (ValueError, TypeError):
                    value = 0
                data_with_values.append((row, value))
            else:
                data_with_values.append((row, 0))
        
        # Sort by measure descending and take top 10
        sorted_data = sorted(data_with_values, key=lambda x: x[1], reverse=True)[:10]
        
        # Extract labels and values
        labels = [str(row[0][category_col_idx]) if category_col_idx < len(row[0]) else "" for row in sorted_data]
        values = [row[1] for row in sorted_data]
        
        category_name = columns[category_col_idx] if category_col_idx < len(columns) else "Category"
        measure_name = columns[measure_col_idx] if measure_col_idx < len(columns) else "Value"
        
        # Use different colors for each bar
        bg_colors = [self.COLORS[i % len(self.COLORS)] for i in range(len(values))]
        border_colors = [self.BORDER_COLORS[i % len(self.BORDER_COLORS)] for i in range(len(values))]
        
        return ChartConfig(
            chart_type="bar",
            labels=labels,
            datasets=[{
                "label": measure_name,
                "data": values,
                "backgroundColor": bg_colors,
                "borderColor": border_colors,
                "borderWidth": 1,
            }],
            options={
                "indexAxis": "y",  # This makes it a horizontal bar chart
                "responsive": True,
                "plugins": {
                    "title": {
                        "display": True,
                        "text": f"Top 10 {category_name} by {measure_name}",
                    },
                    "legend": {
                        "display": False,
                    },
                },
                "scales": {
                    "x": {
                        "title": {
                            "display": True,
                            "text": measure_name,
                        },
                        "beginAtZero": True,
                    },
                    "y": {
                        "title": {
                            "display": True,
                            "text": category_name,
                        },
                    },
                },
            },
            title=f"Top 10 {category_name} by {measure_name}",
        )


# Singleton instance for reuse
_global_visualizer: Optional[ChartVisualizer] = None


def get_visualizer() -> ChartVisualizer:
    """Get or create the global chart visualizer."""
    global _global_visualizer
    if _global_visualizer is None:
        _global_visualizer = ChartVisualizer()
    return _global_visualizer


def create_chart_visualization(
    columns: List[str],
    data: List[List[Any]],
    user_query: str = "",
    formatted_response: str = "",
    chart_metadata: Optional[ChartMetadata] = None,
    metric_name: Optional[str] = None,
    dimension: Optional[str] = None,
    dimension_type: str = "none",
) -> VisualizationResult:
    """
    Create a chart visualization from DAX results.
    
    This is the main entry point for the tool.
    
    Args:
        columns: List of column names
        data: List of rows (each row is a list of values)
        user_query: Original user question (for context)
        formatted_response: Pre-formatted text response to include
        chart_metadata: ChartMetadata object (if provided, overrides individual params)
        metric_name: Primary metric being charted (e.g., 'CSAT', 'Total Transactions')
        dimension: Dimension column for grouping (e.g., 'DimDate[Month]', 'DimServiceUni[Service_Name]')
        dimension_type: 'date', 'categorical', or 'none'
        
    Returns:
        VisualizationResult with chart config and formatted response
        
    Example:
        >>> result = create_chart_visualization(
        ...     columns=["Year", "TotalRevenue"],
        ...     data=[[2024, 1000000], [2025, 1200000]],
        ...     user_query="What is total revenue by year?",
        ...     formatted_response="Revenue increased 20% from 2024 to 2025",
        ...     metric_name="TotalRevenue",
        ...     dimension="Year",
        ...     dimension_type="date",
        ... )
        >>> if result.success and result.chart_config:
        ...     print(result.chart_config.to_json())
    """
    # Build chart_metadata if not provided
    if chart_metadata is None and (metric_name or dimension or dimension_type != "none"):
        chart_metadata = ChartMetadata(
            metric_name=metric_name,
            dimension=dimension,
            dimension_type=dimension_type,
        )
    
    visualizer = get_visualizer()
    return visualizer.create_visualization(
        columns=columns,
        data=data,
        user_query=user_query,
        formatted_response=formatted_response,
        chart_metadata=chart_metadata,
    )


def extract_chart_metadata_from_dax(dax_query: str, columns: List[str], user_query: str = "") -> ChartMetadata:
    """
    Extract chart metadata from a DAX query using heuristics (no LLM needed).
    
    This is a fallback when validation is skipped (optimistic execution path).
    It analyzes the DAX structure and column names to determine:
    - The primary metric (measure)
    - The dimension (grouping column)
    - The dimension type (date vs categorical)
    
    Args:
        dax_query: The DAX query string
        columns: Result column names from execution
        user_query: Original user query for context hints
        
    Returns:
        ChartMetadata with inferred metric_name, dimension, and dimension_type
    """
    # Date-related patterns
    date_patterns = [
        r'date', r'time', r'year', r'month', r'quarter', r'week', r'day',
        r'period', r'mon-yy', r'qrt', r'fiscal', r'calendar'
    ]
    
    # Entity/categorical patterns
    entity_patterns = [
        r'service', r'entity', r'name', r'adge', r'shortname', r'englishname',
        r'status', r'category', r'type', r'region', r'city', r'department'
    ]
    
    # Measure patterns (likely to be the metric)
    measure_patterns = [
        r'total', r'count', r'sum', r'avg', r'percentage', r'score', r'sla',
        r'csat', r'ces', r'nps', r'transactions', r'feedback', r'responses'
    ]
    
    # User query to metric column mapping - these are the PRIMARY metrics user might ask about
    user_metric_hints = {
        'csat': ['happy', 'csat', 'satisfaction', 'happy feedback percentage'],
        'ces': ['effort', 'ces', 'customer effort'],
        'nps': ['nps', 'net promoter', 'promoter score'],
        'sla': ['sla', 'service level'],
        'transactions': ['transactions', 'total transactions', 'transaction count'],
        'feedback': ['feedback responses', 'responses received', 'total feedback'],
    }
    
    user_lower = user_query.lower()
    
    # Initialize results
    metric_name = None
    dimension = None
    dimension_type = "none"
    
    # First, try to find the metric based on user query hints
    # This ensures we use the metric the user is actually asking about
    preferred_metric = None
    for hint_key, hint_patterns in user_metric_hints.items():
        if hint_key in user_lower:
            # User mentioned this metric - find matching column
            for col in columns:
                col_lower = col.lower()
                if any(pattern in col_lower for pattern in hint_patterns):
                    preferred_metric = col
                    break
            if preferred_metric:
                break
    
    # Find dimension and metric from columns
    dimension_idx = None
    metric_idx = None
    
    for idx, col in enumerate(columns):
        col_lower = col.lower()
        
        # Skip boolean/total columns
        if 'isgrandtotal' in col_lower or 'subtotal' in col_lower:
            continue
        
        # Check if this is a date dimension
        is_date = any(re.search(pattern, col_lower) for pattern in date_patterns)
        
        # Check if this is a categorical dimension
        is_categorical = any(re.search(pattern, col_lower) for pattern in entity_patterns)
        
        # Check if this is a measure
        is_measure = any(re.search(pattern, col_lower) for pattern in measure_patterns)
        
        if is_date and dimension is None:
            dimension = col
            dimension_type = "date"
            dimension_idx = idx
        elif is_categorical and dimension is None:
            dimension = col
            dimension_type = "categorical"
            dimension_idx = idx
        elif is_measure and metric_name is None:
            metric_name = col
            metric_idx = idx
    
    # Override metric_name with preferred_metric if we found one from user query
    if preferred_metric:
        metric_name = preferred_metric
    
    # If no dimension found but we have multiple rows with first column being text
    if dimension is None and len(columns) > 1:
        dimension = columns[0]
        # Infer type from user query
        if any(word in user_lower for word in ['trend', 'monthly', 'yearly', 'over time', 'by month', 'by year']):
            dimension_type = "date"
        elif any(word in user_lower for word in ['by service', 'by entity', 'top', 'which', 'per service', 'per entity']):
            dimension_type = "categorical"
    
    # If no metric found, use last numeric-looking column
    if metric_name is None and len(columns) > 1:
        metric_name = columns[-1]
    
    # Check DAX for ROW() which indicates single aggregated value (no chart)
    if 'ROW(' in dax_query.upper():
        dimension_type = "none"
    
    # Check DAX for SUMMARIZECOLUMNS with no grouping columns (just filters)
    if dimension_type == "none":
        # Look for SUMMARIZECOLUMNS pattern
        summarize_match = re.search(r'SUMMARIZECOLUMNS\s*\(([^)]*)', dax_query, re.IGNORECASE | re.DOTALL)
        if summarize_match:
            first_args = summarize_match.group(1)
            # If first arg starts with __ (filter table), no grouping
            if first_args.strip().startswith('__'):
                dimension_type = "none"
    
    return ChartMetadata(
        metric_name=metric_name,
        dimension=dimension,
        dimension_type=dimension_type,
    )
