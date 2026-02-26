"""
Evaluation Script: Run DAX Agent on Ground Truth Queries

This script:
1. Reads ground_truth_queries.csv
2. Runs the SAME workflow as run_devui.py for each query
3. Saves results to a new CSV with the generated DAX column

Uses the shared agent_workflow module for consistency with DevUI.
Uses service principal authentication for Power BI (no user login required).
"""

import os
import csv
import sys
from pathlib import Path
from datetime import datetime

# ===== CONFIGURATION =====
# Set to 0 to start from the beginning, or a 1-based index to skip earlier queries
START_FROM_INDEX = 1  # Start from query 1 (run all)
# =========================

# Add project root to path (go up from evaluations -> backend -> project root)
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables
from dotenv import load_dotenv
load_dotenv(project_root / ".env")

# Import workflow components from shared module
from backend.agent_workflow import (
    create_dax_workflow,
    run_workflow_sync,
)
from backend.executors.workflow_state import get_workflow_state, reset_workflow_state


def get_service_principal_token() -> str:
    """Get a Power BI access token using service principal credentials."""
    from azure.identity import ClientSecretCredential
    
    tenant_id = os.getenv("TENANT_ID")
    # Use Power BI credentials (CLIENT_ID_POWERBI, CLIENT_SECRET_POWERBI)
    client_id = os.getenv("CLIENT_ID_POWERBI")
    client_secret = os.getenv("CLIENT_SECRET_POWERBI")
    
    if not all([tenant_id, client_id, client_secret]):
        raise ValueError("Missing service principal credentials in .env file (TENANT_ID, CLIENT_ID_POWERBI, CLIENT_SECRET_POWERBI)")
    
    print(f"[AUTH] Acquiring Power BI token via service principal...")
    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )
    token = credential.get_token("https://analysis.windows.net/powerbi/api/.default")
    print(f"[AUTH] Token acquired successfully")
    return token.token


def run_evaluation():
    print("=" * 60)
    print("DAX Agent Evaluation (using Workflow)")
    print("=" * 60)
    
    # Get service principal token for Power BI access
    access_token = get_service_principal_token()
    
    # Read ground truth queries
    input_file = project_root / "backend" / "evaluations" / "ground_truth_queries.csv"
    
    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    # Filter out empty rows
    rows = [r for r in rows if r['Query'].strip()]
    
    print(f"\nFound {len(rows)} queries to evaluate\n")
    
    # Create workflow - don't pre-connect (we'll set token manually)
    print("Initializing workflow...")
    workflow, shared_instances = create_dax_workflow(pre_connect_powerbi=False)
    
    # Set the service principal token on the executor
    from backend.tools.execute_dax import get_executor
    executor = get_executor()
    executor.set_access_token(access_token)
    print("[AUTH] Token set on executor")
    print()
    
    # Prepare output file (create early so we can save incrementally)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = project_root / "backend" / "evaluations" / f"evaluation_results_{timestamp}.csv"
    
    fieldnames = ['Query', 'DAX', 'Generated_DAX', 'Final_DAX', 'Intent', 
                  'Was_Corrected', 'Initial_Execution_Failed', 'Execution_Success', 'Row_Count', 
                  'Time_Extract_Intent', 'Time_Generate_DAX', 'DAX_Gen_TTFT', 'DAX_Gen_TTLT',
                  'Time_Validate_DAX', 'Time_Execute_DAX', 'Time_Format_Results', 
                  'Elapsed_Time', 'Success', 'Answer', 'LLM_Provider']
    
    def save_results_to_file(results_list):
        """Save current results to CSV file."""
        with open(output_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results_list)
    
    # Process each query
    results = []
    
    # Apply START_FROM_INDEX - skip earlier queries
    start_idx = max(0, START_FROM_INDEX - 1)  # Convert 1-based to 0-based
    total_original = len(rows)
    if start_idx > 0:
        print(f"\n[INFO] Skipping first {start_idx} queries, starting from query {START_FROM_INDEX}")
        rows = rows[start_idx:]
    
    for i, row in enumerate(rows, START_FROM_INDEX if START_FROM_INDEX > 0 else 1):
        query = row['Query']
        ground_truth_dax = row.get('DAX', '')
        
        print(f"\n{'='*60}")
        print(f"Query {i}/{total_original}: {query[:60]}...")
        print("=" * 60)
        
        # Reset state for each query
        reset_workflow_state()
        
        # Run workflow synchronously
        result = run_workflow_sync(workflow, query, timeout=120)
        
        # Get state for additional details
        state = get_workflow_state()
        
        if result.get("success"):
            print(f"  [OK] Workflow completed successfully")
            print(f"  Intent: {state.intent.upper() if state.intent else 'unknown'}")
            print(f"  Time: {result.get('elapsed_time', 0):.2f}s")
            if state.dax_generation_ttft:
                print(f"  TTFT: {state.dax_generation_ttft:.3f}s, TTLT: {state.dax_generation_ttlt:.3f}s")
            
            # Get step timings
            timings = state.step_timings or {}
            
            results.append({
                'Query': query,
                'DAX': ground_truth_dax,
                'Generated_DAX': state.generated_dax or '',
                'Final_DAX': state.final_dax or state.generated_dax or '',
                'Intent': state.intent or 'unknown',
                'Was_Corrected': bool(state.corrected_dax and state.corrected_dax != state.generated_dax),
                'Initial_Execution_Failed': state.initial_execution_failed,
                'Execution_Success': state.execution_success,
                'Row_Count': state.row_count,
                'Time_Extract_Intent': round(timings.get('extract_intent', 0), 2),
                'Time_Generate_DAX': round(timings.get('generate_dax', 0), 2),
                'DAX_Gen_TTFT': round(state.dax_generation_ttft, 3) if state.dax_generation_ttft else '',
                'DAX_Gen_TTLT': round(state.dax_generation_ttlt, 3) if state.dax_generation_ttlt else '',
                'Time_Validate_DAX': round(timings.get('validate_dax', 0), 2),
                'Time_Execute_DAX': round(timings.get('execute_dax', 0), 2),
                'Time_Format_Results': round(timings.get('format_results', 0), 2),
                'Elapsed_Time': round(result.get('elapsed_time', 0), 2),
                'Success': True,
                'Answer': state.formatted_answer or '',
                'LLM_Provider': os.getenv('LLM_PROVIDER', 'azure'),
            })
        else:
            error = result.get("error", state.error or "Unknown error")
            print(f"  [ERROR] {error}")
            
            # Get step timings (partial if workflow failed midway)
            timings = state.step_timings or {}
            
            results.append({
                'Query': query,
                'DAX': ground_truth_dax,
                'Generated_DAX': state.generated_dax or f"ERROR: {error}",
                'Final_DAX': state.final_dax or '',
                'Intent': state.intent or 'unknown',
                'Was_Corrected': False,
                'Initial_Execution_Failed': state.initial_execution_failed,
                'Execution_Success': False,
                'Row_Count': 0,
                'Time_Extract_Intent': round(timings.get('extract_intent', 0), 2),
                'Time_Generate_DAX': round(timings.get('generate_dax', 0), 2),
                'DAX_Gen_TTFT': round(state.dax_generation_ttft, 3) if state.dax_generation_ttft else '',
                'DAX_Gen_TTLT': round(state.dax_generation_ttlt, 3) if state.dax_generation_ttlt else '',
                'Time_Validate_DAX': round(timings.get('validate_dax', 0), 2),
                'Time_Execute_DAX': round(timings.get('execute_dax', 0), 2),
                'Time_Format_Results': round(timings.get('format_results', 0), 2),
                'Elapsed_Time': round(result.get('elapsed_time', 0), 2),
                'Success': False,
                'Answer': state.formatted_answer or '',
                'LLM_Provider': os.getenv('LLM_PROVIDER', 'azure'),
            })
        
        # Save results every 5 queries
        if len(results) % 5 == 0:
            save_results_to_file(results)
            print(f"  [CHECKPOINT] Saved {len(results)} results to file")
    
    # Final save
    save_results_to_file(results)
    
    # Print summary
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    
    llm_provider = os.getenv('LLM_PROVIDER', 'azure')
    print(f"  LLM Provider: {llm_provider.upper()}")
    
    successful = sum(1 for r in results if r['Success'])
    executed = sum(1 for r in results if r.get('Execution_Success', False))
    corrected = sum(1 for r in results if r.get('Was_Corrected', False))
    initial_failed = sum(1 for r in results if r.get('Initial_Execution_Failed', False))
    total_time = sum(r.get('Elapsed_Time', 0) for r in results)
    
    print(f"  Successful: {successful}/{len(results)}")
    print(f"  Executed against Power BI: {executed}/{len(results)}")
    print(f"  Corrected by validator: {corrected}/{len(results)}")
    print(f"  Initial execution failed: {initial_failed}/{len(results)}")
    print(f"  Failed: {len(results) - successful}/{len(results)}")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Avg time per query: {total_time/len(results):.2f}s")
    
    # Timing breakdown averages
    print(f"\n  Timing Breakdown (averages):")
    avg_extract = sum(r.get('Time_Extract_Intent', 0) for r in results) / len(results)
    avg_generate = sum(r.get('Time_Generate_DAX', 0) for r in results) / len(results)
    avg_validate = sum(r.get('Time_Validate_DAX', 0) for r in results) / len(results)
    avg_execute = sum(r.get('Time_Execute_DAX', 0) for r in results) / len(results)
    avg_format = sum(r.get('Time_Format_Results', 0) for r in results) / len(results)
    
    # TTFT/TTLT averages (only count non-empty values)
    ttft_values = [r['DAX_Gen_TTFT'] for r in results if r.get('DAX_Gen_TTFT')]
    ttlt_values = [r['DAX_Gen_TTLT'] for r in results if r.get('DAX_Gen_TTLT')]
    avg_ttft = sum(ttft_values) / len(ttft_values) if ttft_values else 0
    avg_ttlt = sum(ttlt_values) / len(ttlt_values) if ttlt_values else 0
    
    print(f"    extract_intent:  {avg_extract:.2f}s")
    print(f"    generate_dax:    {avg_generate:.2f}s")
    print(f"      └─ TTFT:       {avg_ttft:.3f}s (avg time to first token)")
    print(f"      └─ TTLT:       {avg_ttlt:.3f}s (avg time to last token)")
    print(f"    validate_dax:    {avg_validate:.2f}s")
    print(f"    execute_dax:     {avg_execute:.2f}s")
    print(f"    format_results:  {avg_format:.2f}s")
    
    print(f"\n  Results saved to: {output_file}")
    
    return output_file


if __name__ == "__main__":
    run_evaluation()
