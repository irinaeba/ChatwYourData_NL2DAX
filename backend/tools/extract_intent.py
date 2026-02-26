"""
TOOL 2: Extract Intent and Relevant Schema

This tool analyzes a user query to determine:
1. The intent/domain (transactions or feedback)
2. Loads the pre-filtered schema for that domain

For TRANSACTIONS domain:
- Reads from cache/schema/schema_transactions.txt

For FEEDBACK domain:
- Reads from cache/schema/schema_feedback.txt

Usage:
    from backend.tools.extract_intent import extract_intent, IntentExtractor
    
    # Simple function call
    result = extract_intent("What are the total transactions by ADGE?")
    
    # Or use the class
    extractor = IntentExtractor()
    result = extractor.extract("Show me NPS scores by entity")
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from enum import Enum


class QueryIntent(Enum):
    """Domain categories for queries."""
    TRANSACTIONS = "transactions"
    FEEDBACK = "feedback"
    UNKNOWN = "unknown"


@dataclass
class IntentResult:
    """Result from intent extraction."""
    success: bool
    intent: str  # "transactions", "feedback", or "unknown"
    confidence: float  # 0.0 to 1.0
    matched_keywords: List[str] = field(default_factory=list)
    extracted_schema: Optional[str] = None
    tables: List[str] = field(default_factory=list)
    measures: List[str] = field(default_factory=list)
    schema_file: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


class IntentExtractor:
    """
    Extracts query intent and loads the appropriate pre-filtered schema.
    
    Uses keyword matching to determine domain, then reads the corresponding
    schema file directly (no parsing needed).
    """
    
    # Keywords that indicate TRANSACTIONS domain
    TRANSACTION_KEYWORDS = [
        # Core terms
        "transaction", "transactions", "application", "applications",
        "service", "services", "completed", "completion",
        # Status terms
        "status", "pending", "in progress", "rejected", "approved",
        # SLA terms
        "sla", "cycle time", "processing time", "turnaround",
        "within sla", "outside sla", "instant",
        # Time bucket terms
        "within 1 day", "above 1 day", "within 6 min",
        # Entity terms for transactions context
        "adge transactions", "entity transactions", "service transactions",
    ]
    
    # Keywords that indicate FEEDBACK domain
    FEEDBACK_KEYWORDS = [
        # Core terms
        "feedback", "satisfaction", "customer feedback", "survey",
        # NPS terms
        "nps", "net promoter", "promoter", "detractor", "passive",
        "recommendation", "promotors", "detractors", "passives",
        # CSAT terms
        "csat", "happy", "sad", "neutral", "smiley", "sentiment",
        # CES terms
        "ces", "customer effort", "effort score", "effort",
        # Specific feedback terms
        "rating", "score", "response", "responses",
    ]
    
    # Schema file paths for each domain
    SCHEMA_FILES = {
        QueryIntent.TRANSACTIONS: "cache/schema/schema_transactions.txt",
        QueryIntent.FEEDBACK: "cache/schema/schema_feedback.txt",
        QueryIntent.UNKNOWN: "cache/schema/schema_tamm.txt",
    }
    
    # Tables for each domain (for metadata purposes)
    DOMAIN_TABLES = {
        QueryIntent.TRANSACTIONS: [
            "FactTransactions",
            "DimADGE",
            "DimServiceUni", 
            "DimMasterStatus",
            "DimDate",
            "_Measures",
        ],
        QueryIntent.FEEDBACK: [
            "FactADFeedback",
            "DimADGE",
            "DimServiceUni",
            "DimDate",
            "TempData",
            "_Measures",
        ],
    }
    
    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize the intent extractor.
        
        Args:
            project_root: Project root directory. Auto-detected if not provided.
        """
        self._project_root = project_root or self._find_project_root()
    
    def _find_project_root(self) -> Path:
        """Find the project root directory."""
        current = Path(__file__).resolve()
        
        # Look for markers of project root
        markers = ['requirements.txt', 'run_devui.py', '.env']
        
        for parent in [current] + list(current.parents):
            if any((parent / marker).exists() for marker in markers):
                return parent
        
        return Path.cwd()
    
    def _detect_intent(self, query: str) -> tuple[QueryIntent, float, List[str]]:
        """
        Detect the query intent based on keywords.
        
        Returns:
            Tuple of (intent, confidence, matched_keywords)
        """
        query_lower = query.lower()
        
        # Count matches for each domain
        transaction_matches = []
        feedback_matches = []
        
        for keyword in self.TRANSACTION_KEYWORDS:
            if keyword.lower() in query_lower:
                transaction_matches.append(keyword)
        
        for keyword in self.FEEDBACK_KEYWORDS:
            if keyword.lower() in query_lower:
                feedback_matches.append(keyword)
        
        trans_score = len(transaction_matches)
        feedback_score = len(feedback_matches)
        
        # Determine intent
        if trans_score == 0 and feedback_score == 0:
            return QueryIntent.UNKNOWN, 0.0, []
        
        if trans_score > feedback_score:
            confidence = min(1.0, trans_score / 3)  # 3+ matches = 100%
            return QueryIntent.TRANSACTIONS, confidence, transaction_matches
        elif feedback_score > trans_score:
            confidence = min(1.0, feedback_score / 3)
            return QueryIntent.FEEDBACK, confidence, feedback_matches
        else:
            # Tie - default to transactions if query mentions specific transaction terms
            if any(kw in query_lower for kw in ["transaction", "application", "sla", "completed"]):
                return QueryIntent.TRANSACTIONS, 0.5, transaction_matches
            else:
                return QueryIntent.FEEDBACK, 0.5, feedback_matches
    
    def _load_schema(self, intent: QueryIntent) -> tuple[str, str]:
        """
        Load the schema file for the given intent.
        
        Returns:
            Tuple of (schema_content, file_path)
        """
        schema_file = self.SCHEMA_FILES.get(intent)
        if not schema_file:
            schema_file = self.SCHEMA_FILES[QueryIntent.UNKNOWN]
        
        full_path = self._project_root / schema_file
        
        if not full_path.exists():
            raise FileNotFoundError(f"Schema file not found: {full_path}")
        
        content = full_path.read_text(encoding='utf-8')
        return content, str(full_path)
    
    def extract(self, query: str) -> IntentResult:
        """
        Extract intent and load the appropriate schema.
        
        Args:
            query: User's natural language question
            
        Returns:
            IntentResult with intent classification and schema content
        """
        if not query or not query.strip():
            return IntentResult(
                success=False,
                intent="unknown",
                confidence=0.0,
                error="Empty query provided"
            )
        
        # Detect intent
        intent, confidence, matched_keywords = self._detect_intent(query)
        
        # Load schema for the detected intent
        try:
            schema_content, schema_file = self._load_schema(intent)
        except FileNotFoundError as e:
            return IntentResult(
                success=False,
                intent=intent.value,
                confidence=confidence,
                matched_keywords=matched_keywords,
                error=str(e)
            )
        
        # Get tables for this domain
        tables = self.DOMAIN_TABLES.get(intent, [])
        
        # Extract measure names from schema (simple parsing)
        measures = []
        for line in schema_content.split('\n'):
            if line.strip().startswith("Measure:"):
                measure_name = line.replace("Measure:", "").strip()
                measures.append(measure_name)
        
        return IntentResult(
            success=True,
            intent=intent.value,
            confidence=confidence,
            matched_keywords=matched_keywords,
            extracted_schema=schema_content,
            tables=tables,
            measures=measures,
            schema_file=schema_file,
        )


def extract_intent(query: str, schema_content: str = None) -> str:
    """
    Extract intent from query - Tool function for agent.
    
    Args:
        query: User's natural language question
        schema_content: Ignored (kept for backward compatibility)
        
    Returns:
        JSON string with intent and schema content
    """
    extractor = IntentExtractor()
    result = extractor.extract(query)
    
    return result.to_json()


# CLI for testing
if __name__ == "__main__":
    import sys
    
    print("=" * 60)
    print("TOOL 2: Extract Intent and Load Schema")
    print("=" * 60)
    
    # Test queries
    test_queries = [
        "What are the total transactions by ADGE?",
        "Show me NPS scores by entity",
        "How many completed transactions in 2025?",
        "What is the customer satisfaction rate?",
        "Show transactions within SLA",
        "What is the CES score?",
    ]
    
    # Get query from command line or use test queries
    if len(sys.argv) > 1:
        queries = [" ".join(sys.argv[1:])]
    else:
        queries = test_queries
    
    extractor = IntentExtractor()
    
    for query in queries:
        print(f"\n📝 Query: {query}")
        print("-" * 40)
        
        result = extractor.extract(query)
        
        if result.success:
            print(f"✅ Intent: {result.intent.upper()}")
            print(f"   Confidence: {result.confidence:.0%}")
            print(f"   Matched keywords: {', '.join(result.matched_keywords)}")
            print(f"   Tables: {', '.join(result.tables)}")
            print(f"   Measures: {len(result.measures)} found")
            print(f"   Schema file: {result.schema_file}")
            print(f"   Schema size: {len(result.extracted_schema):,} chars")
        else:
            print(f"❌ Error: {result.error}")
