"""
Launch DevUI for NL-to-DAX Agent

This script launches the Microsoft Agent Framework DevUI to visualize
tool calls, agent execution, and workflow steps.

Usage:
    python run_devui.py

Then open http://localhost:8080 in your browser.
"""

import os
import sys
import asyncio
import json
import concurrent.futures
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_framework import ChatAgent
from agent_framework.azure import AzureOpenAIChatClient
from agent_framework.devui import serve
from azure.identity import ClientSecretCredential

# Import only the config (avoid circular imports)
from tools.auth import AzureOpenAIConfig


def load_schema_context():
    """Load cached schema or return None."""
    from pathlib import Path
    from datetime import datetime
    
    cache_dir = Path("cache/schema")
    if not cache_dir.exists():
        return None
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Try schema_pack format first (what the app uses)
    cache_file = cache_dir / f"schema_pack_{today}.txt"
    if cache_file.exists():
        print(f"✓ Loading cached schema from {cache_file.name}")
        return cache_file.read_text(encoding="utf-8")
    
    # Try schema_ format
    cache_file = cache_dir / f"schema_{today}.txt"
    if cache_file.exists():
        print(f"✓ Loading cached schema from {cache_file.name}")
        return cache_file.read_text(encoding="utf-8")
    
    # Try to find any recent schema file
    schema_files = sorted(cache_dir.glob("schema*.txt"), reverse=True)
    if schema_files:
        print(f"✓ Loading schema from {schema_files[0].name}")
        return schema_files[0].read_text(encoding="utf-8")
    
    return None


def create_dax_agent():
    """Create the DAX agent with tools for DevUI."""
    
    config = AzureOpenAIConfig()
    
    # Get schema context
    schema_context = load_schema_context()
    if not schema_context:
        print("⚠️ No cached schema found. Run the main app first to extract schema.")
        schema_context = "Schema not available - run main app first to connect to Power BI."
    
    # Create credential for Azure OpenAI
    credential = ClientSecretCredential(
        tenant_id=config.tenant_id,
        client_id=config.client_id,
        client_secret=config.client_secret,
    )
    
    # Create chat client
    chat_client = AzureOpenAIChatClient(
        endpoint=config.endpoint,
        credential=credential,
        deployment_name=config.deployment_name,
        api_version=config.api_version,
    )
    
    # Create a simple DAX generator using Semantic Kernel directly
    # (avoiding the circular import from tools.generate_dax)
    from semantic_kernel import Kernel
    from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
    from semantic_kernel.contents.chat_history import ChatHistory
    from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.azure_chat_prompt_execution_settings import (
        AzureChatPromptExecutionSettings,
    )
    from tools.auth import AzureOpenAIAuthProvider
    
    # DAX generator prompt (copied from agents/prompts.py to avoid circular import)
    # Note: Using double braces {{ }} to escape them from .format()
    DAX_GENERATOR_PROMPT = """You are a DAX query expert. Generate DAX queries for Power BI datasets.

RULES:
1. Always use EVALUATE statement
2. Use proper table and column references from the schema
3. Return ONLY valid JSON with this structure:
   {{"query": "EVALUATE ...", "explanation": "..."}}
4. If you cannot generate a query, return:
   {{"error": "explanation"}}

SCHEMA:
{schema}

Generate a DAX query for the user's question."""
    
    # Initialize Semantic Kernel for DAX generation
    kernel = Kernel()
    auth_provider = AzureOpenAIAuthProvider(config=config)
    chat_service = AzureChatCompletion(
        ad_token_provider=auth_provider.token_provider,
        deployment_name=config.deployment_name,
        endpoint=config.endpoint,
        api_version=config.api_version,
    )
    kernel.add_service(chat_service)
    
    # Store for state
    dax_state = {"generated_dax": None}
    
    # Define tools for the agent
    # Note: These are simplified versions for DevUI visualization
    # The actual workflow uses more complex state management
    
    def generate_dax(user_question: str) -> str:
        """Generate a DAX query from a natural language question."""
        
        print(f"\n🔧 generate_dax called with: {user_question[:80]}...")
        
        try:
            # Create chat history with system prompt
            system_prompt = DAX_GENERATOR_PROMPT.format(schema=schema_context[:10000])
            chat_history = ChatHistory(system_message=system_prompt)
            chat_history.add_user_message(user_question)
            
            # Generate DAX using Semantic Kernel
            # Note: Use max_completion_tokens for newer models (gpt-5-mini, o1, etc.)
            # Note: gpt-5-mini doesn't support temperature parameter
            # Note: reasoning_effort='low' reduces reasoning tokens for faster responses
            settings = AzureChatPromptExecutionSettings(
                max_completion_tokens=4000,
                extra_body={
                    "reasoning_effort": "low",
                },
            )
            
            async def generate():
                return await chat_service.get_chat_message_content(
                    chat_history=chat_history,
                    settings=settings,
                )
            
            # Run async code in a separate thread with its own event loop
            # (avoids "cannot run event loop while another loop is running" error)
            def run_in_thread():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(generate())
                finally:
                    new_loop.close()
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_thread)
                response = future.result(timeout=60)  # 60 second timeout
            
            # Extract response text - str(response) returns the content directly
            raw_response = str(response)
            
            # Fallback: if str(response) returns empty
            if not raw_response or not raw_response.strip():
                content = getattr(response, "content", None)
                if isinstance(content, str) and content.strip():
                    raw_response = content
                elif isinstance(content, list) and content:
                    parts = [str(item.text if hasattr(item, "text") else item) for item in content if item]
                    raw_response = "\n".join(parts)
            
            # Parse the JSON response
            clean_response = raw_response.strip()
            if clean_response.startswith("```"):
                lines = clean_response.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].strip() == "```":
                    lines = lines[:-1]
                clean_response = "\n".join(lines)
            
            parsed = json.loads(clean_response)
            dax_query = parsed.get("query")
            
            if dax_query:
                dax_state["generated_dax"] = dax_query
                print(f"✅ Generated DAX: {dax_query[:100]}...")
                return json.dumps({
                    "success": True,
                    "dax_query": dax_query,
                    "question": user_question
                })
            else:
                return json.dumps({"success": False, "error": parsed.get("error", "No query generated")})
                
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return json.dumps({"success": False, "error": str(e)})
    
    # Initialize DAX executor for real Power BI execution
    dax_executor = None
    try:
        from tools.auth import load_environment, AuthenticationManager
        from tools.execute_dax import DaxQueryExecutor
        
        tenant_id, client_id, client_secret, workspace_name, database_name, adomd_dll = load_environment()
        
        # Get Power BI access token using AuthenticationManager
        auth_manager = AuthenticationManager(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret  # Uses client credentials flow
        )
        access_token = auth_manager.acquire_token()
        
        dax_executor = DaxQueryExecutor(adomd_dll, workspace_name, database_name, access_token)
        dax_executor.connect()
        print("✓ Connected to Power BI for real DAX execution")
    except Exception as e:
        print(f"⚠️ Could not connect to Power BI: {e}")
        print("   DAX execution will be mocked")
    
    def execute_dax(dax_query: str) -> str:
        """Execute a DAX query against Power BI."""
        
        print(f"\n🔧 execute_dax called with: {dax_query[:80]}...")
        
        # If we have a real executor, use it
        if dax_executor:
            try:
                result = dax_executor.execute_with_metadata(dax_query)
                dax_state["generated_dax"] = dax_query
                
                row_count = result.get('row_count', 0)
                columns = result.get("columns", [])
                data = result.get("data", [])
                
                print(f"✅ Query executed: {row_count} rows returned")
                
                return json.dumps({
                    "success": True,
                    "row_count": row_count,
                    "columns": columns,
                    "data": data[:50],  # Limit to 50 rows
                    "truncated": row_count > 50,
                    "dax_query": dax_query
                }, default=str)
            except Exception as e:
                print(f"❌ Execution error: {e}")
                return json.dumps({"success": False, "error": str(e)})
        
        # Fallback to mock if no executor
        return json.dumps({
            "success": False,
            "error": "Power BI connection not available. Check your .env configuration.",
            "dax_query": dax_query
        })
    
    # Build system prompt
    system_prompt = f"""You are a DAX query expert. Answer questions about Power BI data.

## TOOLS (USE IN ORDER, EXACTLY ONCE EACH)
1. generate_dax - Call FIRST with user question
2. execute_dax - Call SECOND with the EXACT dax_query from generate_dax response

## WORKFLOW (ONE ITERATION ONLY)
1. Call generate_dax(user_question="<question>") - ONCE
2. Get the dax_query from the response
3. Call execute_dax(dax_query="<EXACT dax_query from step 2>") - ONCE
4. Format results as markdown table
5. STOP - Your response is complete. Do NOT call tools again.

## CRITICAL RULES
- Call each tool EXACTLY ONCE per user message
- NEVER repeat tool calls for the same question
- After formatting results, your job is DONE - stop processing
- Do not loop back and call tools again

DATABASE SCHEMA:
{schema_context[:5000] if schema_context else "Schema not available"}...

After formatting execute_dax results, finish your response immediately."""

    # Create the agent
    agent = ChatAgent(
        name="DAXAgent",
        chat_client=chat_client,
        instructions=system_prompt,
        tools=[generate_dax, execute_dax]
    )
    
    return agent


def main():
    print("="*60)
    print("🚀 Starting DevUI for NL-to-DAX Agent")
    print("="*60)
    print()
    print("This will launch the Microsoft Agent Framework DevUI")
    print("to visualize tool calls and agent execution.")
    print()
    print("📋 Features:")
    print("  - See tool calls in real-time")
    print("  - Track agent reasoning steps")
    print("  - Debug workflow execution")
    print("  - OpenTelemetry traces")
    print()
    
    # Create the agent
    agent = create_dax_agent()
    
    print("✓ Agent created with tools: generate_dax, execute_dax")
    print()
    print("🌐 Opening DevUI at http://localhost:8080")
    print("   Press Ctrl+C to stop")
    print("="*60)
    
    # Launch DevUI
    serve(
        entities=[agent],
        port=8080,
        auto_open=True  # Automatically open browser
    )


if __name__ == "__main__":
    main()
