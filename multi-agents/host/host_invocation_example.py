import asyncio
import json
import logging
import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote

import boto3
import httpx
import requests
import yaml
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300  # set request timeout to 5 minutes

# Global agent configuration cache
agent_configs = {}

def load_agent_configs():
    """Load agent configurations from config.yaml file."""
    global agent_configs
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
        for agent_config in config.get("agents", []):
            agent_configs[agent_config["name"]] = agent_config
    logger.info(f"Loaded {len(agent_configs)} agent configurations")

def fetch_ssm_parameter(parameter_path: str, region: str) -> dict:
    """Fetch IDP configuration from SSM Parameter Store."""
    ssm = boto3.client("ssm", region_name=region)
    response = ssm.get_parameter(Name=parameter_path, WithDecryption=True)
    config_str = response["Parameter"]["Value"]
    return json.loads(config_str)

async def get_bearer_token(idp_config: dict) -> str:
    """Get OAuth bearer token using client credentials flow."""
    domain = idp_config["domain"]
    region = idp_config["user_pool_id"].split("_")[0]
    token_endpoint = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"
    
    scopes = idp_config.get("scopes", [])
    resource_server = idp_config["resource_server_identifier"]
    scope_str = " ".join([f"{resource_server}/{scope}" for scope in scopes])
    
    token_data = {
        "grant_type": "client_credentials",
        "client_id": idp_config["client_id"],
        "client_secret": idp_config["client_secret"],
        "scope": scope_str,
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            token_endpoint,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token_response = response.json()
        return token_response["access_token"]

def format_agent_response(response):
    """Extract and format agent response for human readability."""
    # Get the main response text from artifacts
    if response.artifacts and len(response.artifacts) > 0:
        artifact = response.artifacts[0]
        if artifact.parts and len(artifact.parts) > 0:
            return artifact.parts[0].root.text
    
    # Fallback: concatenate all agent messages from history
    agent_messages = [
        msg.parts[0].root.text 
        for msg in response.history 
        if msg.role.value == 'agent' and msg.parts
    ]
    return ''.join(agent_messages)

def format_agent_trace(response):
    """Format agent response as a readable trace of calls."""
    print("=" * 60)
    print("🔍 AGENT EXECUTION TRACE")
    print("=" * 60)
    
    # Context info
    print(f"📋 Context ID: {response.context_id}")
    print(f"🆔 Task ID: {response.id}")
    print(f"📊 Status: {response.status.state.value}")
    print(f"⏰ Completed: {response.status.timestamp}")
    print()
    
    # Trace through history
    print("🔄 EXECUTION FLOW:")
    print("-" * 40)
    
    for i, msg in enumerate(response.history, 1):
        role_icon = "👤" if msg.role.value == "user" else "🤖"
        text = msg.parts[0].root.text if msg.parts else "[No content]"
        
        # Truncate long messages for trace view
        if len(text) > 80:
            text = text[:77] + "..."
            
        print(f"{i:2d}. {role_icon} {msg.role.value.upper()}: {text}")
    
    print()
    print("✅ FINAL RESULT:")
    print("-" * 40)
    
    # Final artifact
    if response.artifacts:
        final_text = response.artifacts[0].parts[0].root.text
        print(final_text[:200] + "..." if len(final_text) > 200 else final_text)
    
    print("=" * 60)

def create_message(*, role: Role = Role.user, text: str) -> Message:
    return Message(
        kind="message",
        role=role,
        parts=[Part(TextPart(kind="text", text=text))],
        message_id=uuid4().hex,
    )

async def send_sync_message(agent_name: str, message: str):
    """Send message to agent by name, fetching config and token automatically."""
    if agent_name not in agent_configs:
        raise ValueError(f"Agent {agent_name} not found in configuration")
    
    config = agent_configs[agent_name]
    runtime_arn = config["runtime_arn"]
    region = config["region"]
    ssm_path = config["ssm_idp_config_path"]
    
    print(f"Connecting to agent: {agent_name}")
    print(f"Runtime ARN: {runtime_arn}")
    print(f"Region: {region}")
    
    try:
        # Fetch IDP config and get bearer token
        idp_config = fetch_ssm_parameter(ssm_path, region)
        bearer_token = await get_bearer_token(idp_config)
        
        escaped_agent_arn = quote(runtime_arn, safe='')
        runtime_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_agent_arn}/invocations/"
        print(f"Runtime URL: {runtime_url}")
    except Exception as e:
        print(f"Error during setup: {e}")
        raise
    
    # Generate a unique session ID
    session_id = str(uuid4())
    print(f"Generated session ID: {session_id}")

    # Add authentication headers for AgentCore
    headers = {"Authorization": f"Bearer {bearer_token}",
              'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id}
        
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers) as httpx_client:
        # Get agent card from the runtime URL
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=runtime_url)
        agent_card = await resolver.get_agent_card()
        print(agent_card)

        # Agent card contains the correct URL (same as runtime_url in this case)
        # No manual override needed - this is the path-based mounting pattern

        # Create client using factory
        config = ClientConfig(
            httpx_client=httpx_client,
            streaming=False,  # Use non-streaming mode for sync response
        )
        factory = ClientFactory(config)
        client = factory.create(agent_card)

        # Create and send message
        msg = create_message(text=message)

        # With streaming=False, this will yield exactly one result
        async for event in client.send_message(msg):
            if isinstance(event, Message):
                logger.info(event.model_dump_json(exclude_none=True, indent=2))
                return event
            elif isinstance(event, tuple) and len(event) == 2:
                # (Task, UpdateEvent) tuple
                task, update_event = event
                logger.info(f"Task: {task.model_dump_json(exclude_none=True, indent=2)}")
                if update_event:
                    logger.info(f"Update: {update_event.model_dump_json(exclude_none=True, indent=2)}")
                return task
            else:
                # Fallback for other response types
                logger.info(f"Response: {str(event)}")
                return event


async def test_host_agent_direct(orchestration_arn: str, region: str, prompt: str):
    """Test host agent using direct HTTP requests (for host orchestrator agent)."""
    # Load host agent config to get SSM path
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    
    # Find host agent config (assuming it's named "Host_Agent" or similar)
    host_config = None
    for agent_config in config.get("host-agent", []):
        if "host" in agent_config["name"].lower() or agent_config["runtime_arn"] == orchestration_arn:
            host_config = agent_config
            break
    
    if not host_config:
        print(f"Host agent config not found for ARN: {orchestration_arn}")
        return
    
    ssm_path = host_config["ssm_idp_config_path"]
    
    try:
        # Fetch IDP config and get bearer token
        idp_config = fetch_ssm_parameter(ssm_path, region)
        bearer_token = await get_bearer_token(idp_config)
        
        session_id = str(uuid4())
        print(f'Invoking for session: {session_id}')
        
        headers = {
            'Authorization': f'Bearer {bearer_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id
        }
        
        payload = {"prompt": prompt}
        
        escaped_agent_arn = quote(orchestration_arn, safe='')
        
        response = requests.post(
            f'https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_agent_arn}/invocations',
            headers=headers,
            data=json.dumps(payload),
            stream=True
        )
        
        print(f"Response status: {response.status_code}")
        
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith('data: '):
                data = line[6:]
                try:
                    parsed = json.loads(data)
                    print(parsed)
                except:
                    print(data)
                    
    except Exception as e:
        print(f"Error testing host agent: {e}")


async def main():
    """Test the send_sync_message function."""
    # Load agent configurations first
    load_agent_configs()
    
    # Test host agent directly (uncomment and set your host agent ARN)
    # ORCHESTRATION_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/your-host-agent-id"
    # REGION = "us-east-1"
    # await test_host_agent_direct(ORCHESTRATION_ARN, REGION, "What is DynamoDB?")
    
    #Test with monitoring agent
    # result = await send_sync_message("Monitoring_Agent", "What are the cloudwatch logs for lambda in my AWS account?")
    # formatted_output = format_agent_response(result)
    # print(f"Monitoring Agent Response:\n{formatted_output}\n")
    
    # # Test with ops agent
    # result = await send_sync_message("OpsRemediation_Agent", "Search for best practices for managing EC2 instance utilization")
    # formatted_output = format_agent_trace(result)
    # print(f"Ops Agent Response:\n{formatted_output}")

    # Example usage for direct host agent testing:
    # Replace with your actual host agent ARN and region
    ORCHESTRATION_ARN = "arn:aws:bedrock-agentcore:us-east-1:206409480438:runtime/host_agent_bedrock-vVFQbzCzs0"
    REGION = "us-east-1"
    await test_host_agent_direct(ORCHESTRATION_ARN, REGION, "Fetch recent cloudwatch logs for lambda named monitoring-agent-fn-new in my AWS account 206409480438 for us-east-1 region for November 9?")


if __name__ == "__main__":
    asyncio.run(main())