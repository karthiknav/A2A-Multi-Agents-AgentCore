from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from google.adk.agents.llm_agent import Agent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from urllib.parse import quote
import boto3
import httpx
import json
import os
import uuid
import yaml
from datetime import datetime

IS_DOCKER = os.getenv("DOCKER_CONTAINER", "0") == "1"

if IS_DOCKER:
    from utils import get_ssm_parameter, get_aws_info
else:
    from host.utils import get_ssm_parameter, get_aws_info


# AWS and agent configuration
account_id, region = get_aws_info()

def load_config():
    config_path = "config.yaml" if IS_DOCKER else "host/config.yaml"
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

config = load_config()


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

async def create_simple_client_factory(agent_config: dict, session_id: str):
    """Create a simple client factory using direct token approach."""
    # Fetch IDP config and get bearer token
    idp_config = fetch_ssm_parameter(agent_config["ssm_idp_config_path"], agent_config["region"])
    bearer_token = await get_bearer_token(idp_config)
    
    runtime_arn = agent_config["runtime_arn"]
    region = agent_config["region"]
    escaped_agent_arn = quote(runtime_arn, safe='')
    runtime_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_agent_arn}/invocations/"
    
    # Add authentication headers
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id
    }
    
    httpx_client = httpx.AsyncClient(timeout=300.0, headers=headers)
    
    # Get agent card
    resolver = A2ACardResolver(httpx_client=httpx_client, base_url=runtime_url)
    agent_card = await resolver.get_agent_card()
    
    # Create client factory
    config = ClientConfig(httpx_client=httpx_client, streaming=False)
    factory = ClientFactory(config)
    
    return factory, agent_card


async def get_root_agent(session_id: str, actor_id: str):
    sub_agents = []
    
    for agent_config in config['agents']:
        agent_card_url = (
            f"https://bedrock-agentcore.{agent_config['region']}.amazonaws.com/runtimes/"
            f"{quote(agent_config['runtime_arn'], safe='')}/invocations/.well-known/agent-card.json"
        )
        
        # Create simple client factory
        factory, agent_card = await create_simple_client_factory(agent_config, session_id)
        
        agent = RemoteA2aAgent(
            name=agent_config['name'].lower().replace('_', '_'),
            description=agent_config['description'],
            agent_card=agent_card_url,
            a2a_client_factory=factory,
        )
        sub_agents.append(agent)

    # Create root agent
    agent_descriptions = "\n".join([f"- {agent.name}: {agent.description}" for agent in sub_agents])
    
    root_agent = Agent(
        model="gemini-2.0-flash",
        name="root_agent",
        sub_agents=sub_agents,
        instruction=_get_system_instruction(agent_descriptions)
    )

    return root_agent

def _get_system_instruction(agent_descriptions) -> str:
        """Generate system instruction for the orchestrator agent."""
        return f"""
Role: You are the Lead Orchestrator, an expert triage and coordination agent for incident response and operations management. Your primary function is to route user requests to the right specialist agent, track progress, and report back clearly.

Specialist Agents Available:

{agent_descriptions}

Core Directives:

1. Initiate Triage: When asked for help, first clarify the objective and relevant scope (AWS account/region/service, time window, urgency).

2. Task Delegation: Use the send_message_to_agent tool to contact the appropriate agent(s).
   - Be explicit: e.g., "Please scan CloudWatch logs and metrics for service X between 2024-08-01 and 2024-08-03."
   - Always pass the official agent name (Monitoring_Agent, OpsRemediation_Agent) when sending messages.

3. Analyze Responses: Correlate findings from all contacted agents. Summarize root causes, evidence (metrics/logs), and proposed actions.

4. Jira Workflow: If Monitoring_Agent reports an issue, ensure a Jira ticket is (or gets) created, capture the ticket ID, status, and assignee, and keep it updated as remediation proceeds.

5. Propose and Confirm: Present recommended actions (and any risk/impact) to the user for confirmation. If the user has pre-approved runbooks, proceed accordingly.

6. Execute Remediation: After confirmation, instruct OpsRemediation_Agent to perform the fix. Track outcomes and validation steps (post-fix metrics, log baselines).

7. Transparent Communication: Relay progress and final results, including Jira IDs/links and any residual follow-ups. Do not ask for permission before contacting specialist agents.

8. Tool Reliance: Strictly rely on available tools to fulfill requests. Do not invent results or act without agent/tool confirmation.

9. Readability: Respond concisely, preferably with bullet points and short sections.

10. Agent Selection: Choose the appropriate agent based on the task:
    - Monitoring_Agent: For AWS metrics, logs, CloudWatch alarms, Jira ticket creation
    - OpsRemediation_Agent: For searching remediation strategies, AWS documentation, troubleshooting guidance

Today's Date (YYYY-MM-DD): {datetime.now().strftime("%Y-%m-%d")}
"""

async def get_agent_and_card(session_id: str, actor_id: str):
    """
    Lazy initialization of the root agent.
    This is called inside the entrypoint where workload identity is available.
    """

    root_agent = await get_root_agent(session_id=session_id, actor_id=actor_id)

    async def get_agents_cards():
        agents_info = {}
        sub_agents = root_agent.sub_agents

        for agent in sub_agents:
            agent_data = {}

            # Access the source URL before resolution
            if hasattr(agent, "_agent_card_source"):
                agent_data["agent_card_url"] = agent._agent_card_source

            # Ensure resolution and access full agent card
            if hasattr(agent, "_ensure_resolved"):
                await agent._ensure_resolved()

                if hasattr(agent, "_agent_card") and agent._agent_card:
                    card = agent._agent_card
                    agent_data["agent_card"] = card.model_dump(exclude_none=True)

            agents_info[agent.name] = agent_data

        return agents_info

    # Get agents cards info
    agents_cards = await get_agents_cards()

    return root_agent, agents_cards


async def main():
    """Test the agent standalone."""
    session_id = str(uuid.uuid4())
    actor_id = "webadk"
    
    print(f"Initializing root agent with session_id: {session_id}")
    
    try:
        root_agent, agents_cards = await get_agent_and_card(session_id=session_id, actor_id=actor_id)
        print(f"Root agent created successfully: {root_agent.name}")
        print(f"Available sub-agents: {[agent.name for agent in root_agent.sub_agents]}")
        
        # Test a simple query
        test_query = "Check CloudWatch logs for any errors in the last hour"
        print(f"\nTesting query: {test_query}")
        
        response = await root_agent.stream(test_query)
        async for event in response:
            print(f"Event: {event}")
            
    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import asyncio
    import datetime
    asyncio.run(main())