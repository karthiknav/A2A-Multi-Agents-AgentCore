# Introducing Agent-to-Agent Protocol Support in Amazon Bedrock AgentCore Runtime

## The Future of Multi-Agent Systems Begins Here

Artificial intelligence has evolved beyond single-agent applications. Modern enterprise challenges demand coordination between multiple specialized AI agents, each excelling at specific tasks while working together toward common goals. Amazon Bedrock AgentCore Runtime now supports the Agent-to-Agent (A2A) protocol, bringing standardized, secure, and interoperable agent collaboration to AWS.

The A2A protocol represents a fundamental shift in how AI agents communicate. Backed by industry leaders including Google, Microsoft, Anthropic, and over 50 technology partners under the Linux Foundation, A2A establishes an open standard for agent-to-agent communication. This protocol enables agents built with different frameworks, deployed across different platforms, and serving different purposes to discover each other, exchange information securely, and coordinate actions seamlessly. Amazon Bedrock AgentCore's support for A2A means developers can now deploy production-ready multi-agent systems with enterprise-grade security, scalability, and observability built in. By combining A2A's standardized communication with AgentCore's managed infrastructure, organizations can focus on building intelligent agent capabilities rather than managing complex coordination logic.

## Understanding the Multi-Agent Communication Landscape

Building effective multi-agent systems requires understanding two complementary protocols: A2A and Model Context Protocol (MCP). While both enable agent ecosystems, they serve distinct purposes. MCP focuses on connecting agents to tools and data sources, providing a standardized way for agents to access databases, APIs, and external services. Think of MCP as giving agents hands to interact with the world around them. A2A, on the other hand, enables agents to communicate with each other in their natural modalities, supporting text, audio, video streaming, and complex task coordination. A2A gives agents a voice to collaborate with their peers.

The A2A protocol brings several critical capabilities to multi-agent architectures. Its modality-agnostic design supports diverse interaction patterns from simple text exchanges to real-time audio and video streaming. Built on JSON-RPC 2.0 over HTTP/S, A2A provides flexible communication patterns including synchronous request-response, server-sent event streaming, and asynchronous push notifications. Agent discovery happens through standardized Agent Cards, JSON metadata documents that describe each agent's identity, capabilities, skills, service endpoints, and authentication requirements. This discovery mechanism enables dynamic agent ecosystems where new agents can join and contribute without manual configuration changes.

## Using A2A with AgentCore Runtime

Amazon Bedrock AgentCore Runtime's A2A support integrates seamlessly with existing AWS services while maintaining protocol transparency. When you deploy an A2A server to AgentCore, the runtime acts as a transparent proxy layer, passing JSON-RPC payloads directly to your containerized agents without modification. This design preserves standard A2A features like agent discovery through Agent Cards served at `/.well-known/agent-card.json`, while adding enterprise authentication through SigV4 and OAuth 2.0, automatic scaling based on demand, and comprehensive observability through CloudWatch and X-Ray. The runtime expects containers to run stateless HTTP servers on port 9000 at the root path, aligning perfectly with standard A2A server conventions.

### Step 1: Create Your A2A Server

Begin by installing the required packages for A2A development:

```bash
pip install strands-agents[a2a]
pip install bedrock-agentcore
pip install strands-agents-tools
```

Create your A2A server file `my_a2a_server.py`:

```python
import logging
import os
from strands_tools.calculator import calculator
from strands import Agent
from strands.multiagent.a2a import A2AServer
import uvicorn
from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)

# Use the complete runtime URL from environment variable, fallback to local
runtime_url = os.environ.get('AGENTCORE_RUNTIME_URL', 'http://127.0.0.1:9000/')
logging.info(f"Runtime URL: {runtime_url}")

strands_agent = Agent(
    name="Calculator Agent",
    description="A calculator agent that can perform basic arithmetic operations.",
    tools=[calculator],
    callback_handler=None
)

host, port = "0.0.0.0", 9000

# Pass runtime_url to http_url parameter AND use serve_at_root=True
a2a_server = A2AServer(
    agent=strands_agent,
    http_url=runtime_url,
    serve_at_root=True  # Serves locally at root (/) regardless of remote URL path complexity
)

app = FastAPI()

@app.get("/ping")
def ping():
    return {"status": "healthy"}

app.mount("/", a2a_server.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
```

**Understanding the code:**
- **Strands Agent**: Creates an agent with specific tools and capabilities
- **A2AServer**: Wraps the agent to provide A2A protocol compatibility
- **Agent Card URL**: Dynamically constructs the correct URL based on deployment context using the `AGENTCORE_RUNTIME_URL` environment variable
- **Port 9000**: A2A servers run on port 9000 by default in AgentCore Runtime

### Step 2: Test Your A2A Server Locally

Start your A2A server:

```bash
python my_a2a_server.py
```

You should see output indicating the server is running on port 9000.

**Invoke the Agent:**

```bash
curl -X POST http://0.0.0.0:9000 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-001",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "what is 101 * 11?"
        }],
        "messageId": "d0673ab9-796d-4270-9435-451912020cd1"
      }
    }
  }' | jq .
```

**Test Agent Card retrieval:**

```bash
curl http://localhost:9000/.well-known/agent-card.json | jq .
```

### Step 3: Deploy Your A2A Server to Bedrock AgentCore Runtime

Install the Amazon Bedrock AgentCore CLI:

```bash
pip install bedrock-agentcore-starter-toolkit
```

Create a project folder with the following structure:

```
your_project_directory/
├── a2a_server.py          # Your main agent code
├── requirements.txt       # Dependencies for your agent
```

Create `requirements.txt` with the following dependencies:

```
strands-agents[a2a]
bedrock-agentcore
strands-agents-tools
```

**Set up Cognito user pool for authentication:**

For detailed Cognito setup instructions, see the [Set up Cognito user pool for authentication](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html#set-up-cognito-user-pool-for-authentication) documentation. This provides the OAuth tokens required for secure access to your deployed server.

**Configure your A2A server for deployment:**

After setting up authentication, create the deployment configuration:

```bash
agentcore configure -e my_a2a_server.py --protocol A2A
```

1. Select protocol as A2A
2. Configure with OAuth configuration as setup in the previous step

**Deploy to AWS:**

```bash
agentcore launch
```

After deployment, you'll receive an agent runtime ARN:

```
arn:aws:bedrock-agentcore:us-west-2:accountId:runtime/my_a2a_server-xyz123
```

### Step 4: Get Agent Card

Agent Cards are JSON metadata documents that describe an A2A server's identity, capabilities, skills, service endpoint, and authentication requirements. They enable automatic agent discovery in the A2A ecosystem.

**Export required environment variables:**

```bash
export BEARER_TOKEN="<BEARER_TOKEN>"
export AGENT_ARN="arn:aws:bedrock-agentcore:us-west-2:accountId:runtime/my_a2a_server-xyz123"
```

**Retrieve Agent Card:**

```python
import os
import json
import requests
from uuid import uuid4
from urllib.parse import quote

def fetch_agent_card():
    agent_arn = os.environ.get('AGENT_ARN')
    bearer_token = os.environ.get('BEARER_TOKEN')

    if not agent_arn or not bearer_token:
        print("Error: AGENT_ARN and BEARER_TOKEN environment variables must be set")
        return

    # URL encode the agent ARN
    escaped_agent_arn = quote(agent_arn, safe='')

    # Construct the URL
    url = f"https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/{escaped_agent_arn}/invocations/.well-known/agent-card.json"

    # Generate a unique session ID
    session_id = str(uuid4())
    print(f"Generated session ID: {session_id}")

    headers = {
        'Accept': '*/*',
        'Authorization': f'Bearer {bearer_token}',
        'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        agent_card = response.json()
        print(json.dumps(agent_card, indent=2))
        return agent_card
    except requests.exceptions.RequestException as e:
        print(f"Error fetching agent card: {e}")
        return None

if __name__ == "__main__":
    fetch_agent_card()
```

Export the runtime URL from the Agent Card:

```bash
export AGENTCORE_RUNTIME_URL="https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/<ARN>/invocations/"
```

### Step 5: Invoke Your Deployed A2A Server

Create `my_a2a_client_remote.py`:

```python
import asyncio
import logging
import os
from uuid import uuid4
import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300  # 5 minutes

def create_message(*, role: Role = Role.user, text: str) -> Message:
    return Message(
        kind="message",
        role=role,
        parts=[Part(TextPart(kind="text", text=text))],
        message_id=uuid4().hex,
    )

async def send_sync_message(message: str):
    runtime_url = os.environ.get('AGENTCORE_RUNTIME_URL')
    session_id = str(uuid4())
    print(f"Generated session ID: {session_id}")

    headers = {
        "Authorization": f"Bearer {os.environ.get('BEARER_TOKEN')}",
        'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id
    }

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers) as httpx_client:
        # Get agent card from the runtime URL
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=runtime_url)
        agent_card = await resolver.get_agent_card()

        # Create client using factory
        config = ClientConfig(httpx_client=httpx_client, streaming=False)
        factory = ClientFactory(config)
        client = factory.create(agent_card)

        # Create and send message
        msg = create_message(text=message)

        async for event in client.send_message(msg):
            if isinstance(event, Message):
                logger.info(event.model_dump_json(exclude_none=True, indent=2))
                return event
            elif isinstance(event, tuple) and len(event) == 2:
                task, update_event = event
                logger.info(f"Task: {task.model_dump_json(exclude_none=True, indent=2)}")
                if update_event:
                    logger.info(f"Update: {update_event.model_dump_json(exclude_none=True, indent=2)}")
                return task
            else:
                logger.info(f"Response: {str(event)}")
                return event

# Usage
asyncio.run(send_sync_message("what is 101 * 11"))
```

This demonstrates the complete workflow for creating, deploying, and invoking A2A servers on Amazon Bedrock AgentCore Runtime.

## Real-World Use Case: Multi-Agent AWS Monitoring System

To demonstrate the power of A2A-enabled multi-agent systems, consider an enterprise AWS monitoring solution. This architecture showcases how specialized agents coordinate to handle complex operational challenges. The system consists of three primary agents, each with distinct responsibilities connected through the A2A protocol.

The Monitoring Agent serves as the operational intelligence layer, continuously analyzing CloudWatch logs, metrics, dashboards, and alarms across AWS services. Built using the Strands agent framework and deployed as an A2A server on AgentCore Runtime, this agent specializes in identifying anomalies, tracking error patterns, and surfacing actionable insights from vast amounts of telemetry data. When unusual patterns emerge, the Monitoring Agent doesn't just report them; it initiates conversations with other specialized agents to coordinate response actions.

The Operational Agent acts as the system's automation engine, capable of executing remediation actions, managing infrastructure configurations, and orchestrating recovery procedures. When the Monitoring Agent detects a critical issue, it communicates directly with the Operational Agent through A2A, providing context about the problem and requesting specific remediation actions. This agent-to-agent conversation happens in real-time, using standardized JSON-RPC messages that preserve semantic meaning across different agent implementations.

The Google ADK Agent demonstrates the cross-platform interoperability that A2A enables. This agent might run on Google Cloud infrastructure using Google's Agent Development Kit, yet communicates seamlessly with AWS-hosted agents through the standardized A2A protocol. It could handle specialized tasks like advanced analytics, machine learning predictions, or integration with Google Workspace tools, contributing its unique capabilities to the overall monitoring ecosystem without requiring custom integration code.

## Implementation Guide: Building the Monitoring Agent A2A Server

Let's walk through implementing the Monitoring Agent as an A2A server on Amazon Bedrock AgentCore Runtime. This implementation demonstrates key concepts including agent creation, A2A server configuration, gateway integration, and runtime deployment.

### Prerequisites and Environment Setup

Before building your A2A-enabled monitoring agent, ensure you have Python 3.11 or higher installed, AWS credentials configured with appropriate permissions for Amazon Bedrock AgentCore, Lambda, Cognito, CloudWatch, and IAM services. You'll also need the Amazon Bedrock AgentCore CLI for deployment operations.

Install the required dependencies:

```bash
pip install strands-agents[a2a] bedrock-agentcore strands-agents-tools
```

The Strands framework provides the agent abstraction layer, while the A2A extension enables Agent-to-Agent protocol support. The AgentCore SDK handles runtime deployment and configuration.

### Creating the Monitoring Agent Server

The monitoring agent implementation centers on three key components: the Strands agent with monitoring capabilities, an A2A server wrapper that exposes the agent through the A2A protocol, and a FastAPI application that hosts the server infrastructure.

Create `monitoring_agent.py`:

```python
import logging
import os
from strands import Agent
from strands.multiagent.a2a import A2AServer
import uvicorn
from fastapi import FastAPI

# Configure logging
logging.basicConfig(level=logging.INFO)

# Get runtime URL from environment (set by AgentCore in production)
runtime_url = os.environ.get('AGENTCORE_RUNTIME_URL', 'http://127.0.0.1:9000/')
logging.info(f"Runtime URL: {runtime_url}")

# Create the monitoring agent with specialized tools
monitoring_agent = Agent(
    name="monitoring_agent",
    description="A monitoring agent that handles CloudWatch logs, metrics, dashboards, and AWS service monitoring",
    system_prompt=MONITORING_PROMPT,  # Your agent's system prompt
    model=bedrock_model,  # Configured Bedrock model
    tools=monitoring_tools,  # CloudWatch monitoring tools
)

# Configure A2A server
host, port = "0.0.0.0", 9000
a2a_server = A2AServer(
    agent=monitoring_agent,
    http_url=runtime_url,
    serve_at_root=True,  # Serves at / path for A2A compliance
    version="1.0.0"
)

# Create FastAPI application
app = FastAPI(title="Monitoring Agent A2A Server")

@app.get("/ping")
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "agent": "monitoring_agent",
        "tools_count": len(monitoring_tools)
    }

# Mount A2A server
fastapi_app = a2a_server.to_fastapi_app()

if __name__ == "__main__":
    logging.info(f"Starting A2A Monitoring Agent on {host}:{port}")
    uvicorn.run(fastapi_app, host=host, port=port)
```

The critical configuration here is `serve_at_root=True`, which ensures the A2A server responds at the root path as required by the A2A specification. The `AGENTCORE_RUNTIME_URL` environment variable enables the agent to construct correct URLs for its Agent Card, whether running locally or in production.

### Local Testing and Validation

Before deploying to production, thoroughly test your A2A server locally. Start the server:

```bash
python monitoring_agent.py
```

Test the A2A message endpoint using a standard JSON-RPC request:

```bash
curl -X POST http://localhost:9000 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-001",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "What are the CloudWatch logs for Lambda in my AWS account?"
        }],
        "messageId": "test-message-001"
      }
    }
  }' | jq .
```

Retrieve and validate the Agent Card:

```bash
curl http://localhost:9000/.well-known/agent-card.json | jq .
```

The Agent Card response reveals the agent's capabilities, supported skills, preferred transport protocol (JSON-RPC), and service endpoint. This metadata enables other agents to discover and interact with your monitoring agent programmatically.

### Deploying to Amazon Bedrock AgentCore Runtime

Production deployment involves several steps: authentication configuration, AgentCore runtime setup, and agent deployment. Set up Amazon Cognito for OAuth authentication following the AgentCore documentation. This provides secure, token-based access control for your deployed agent.

Configure your A2A server for deployment using the AgentCore CLI:

```bash
agentcore configure -e monitoring_agent.py --protocol A2A
```

The CLI prompts you through selecting the A2A protocol, configuring OAuth credentials from your Cognito setup, and specifying runtime execution roles. Deploy your agent to AWS:

```bash
agentcore launch
```

The deployment process packages your agent code, creates a container image, pushes it to Amazon ECR, and launches an AgentCore runtime instance. Upon completion, you receive an agent runtime ARN similar to:

```
arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/monitoring_agent-abc123
```

### Invoking the Deployed Agent

Interacting with your deployed A2A agent requires authentication and proper URL construction. Set environment variables for access:

```bash
export BEARER_TOKEN="<your-oauth-token>"
export AGENTCORE_RUNTIME_URL="https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/<encoded-arn>/invocations/"
```

Create an A2A client to communicate with your deployed agent:

```python
import asyncio
import os
from uuid import uuid4
import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart

async def invoke_monitoring_agent(query: str):
    runtime_url = os.environ.get('AGENTCORE_RUNTIME_URL')
    session_id = str(uuid4())

    headers = {
        "Authorization": f"Bearer {os.environ.get('BEARER_TOKEN')}",
        'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id
    }

    async with httpx.AsyncClient(timeout=300, headers=headers) as client:
        # Discover agent through its card
        resolver = A2ACardResolver(httpx_client=client, base_url=runtime_url)
        agent_card = await resolver.get_agent_card()

        # Create A2A client for this agent
        config = ClientConfig(httpx_client=client, streaming=False)
        factory = ClientFactory(config)
        a2a_client = factory.create(agent_card)

        # Send message
        message = Message(
            kind="message",
            role=Role.user,
            parts=[Part(TextPart(kind="text", text=query))],
            message_id=uuid4().hex,
        )

        async for response in a2a_client.send_message(message):
            return response

# Invoke the agent
result = asyncio.run(invoke_monitoring_agent(
    "Analyze recent errors in Lambda function logs"
))
```

This client demonstrates the complete A2A workflow: discovering the agent through its Agent Card, establishing an authenticated session, and exchanging messages using standard A2A types. The protocol abstraction means this same client code works with any A2A-compliant agent, regardless of implementation framework or hosting platform.

## Multi-Agent Collaboration in Action

The true power of A2A emerges when multiple agents collaborate on complex tasks. In our monitoring scenario, consider what happens when a critical Lambda function error spike occurs. The Monitoring Agent detects the anomaly through CloudWatch metrics analysis and formulates a structured problem description. Using A2A, it sends a message to the Operational Agent:

```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{
        "kind": "text",
        "text": "Detected 85% error rate in Lambda function 'OrderProcessor' over last 10 minutes. Error pattern indicates database connection timeouts. Request analysis and remediation recommendations."
      }],
      "messageId": "incident-12345"
    }
  }
}
```

The Operational Agent receives this request, analyzes recent deployments, checks database connection pools, and identifies a configuration change as the likely cause. It responds with structured recommendations and offers to execute rollback procedures. Meanwhile, the Google ADK Agent, monitoring the same conversation, contributes historical trend analysis from its data store, helping contextualize whether this represents normal variance or a true anomaly.

This multi-agent conversation happens through standardized A2A messages, with each agent maintaining its specialty while contributing to the collective problem-solving process. AgentCore Runtime manages the infrastructure complexity, session isolation, and observability, allowing the agents to focus on their core intelligence.

## Enterprise Considerations and Best Practices

Deploying A2A-enabled multi-agent systems in production requires attention to several operational concerns. Security starts with proper authentication configuration through Cognito or your preferred identity provider, with OAuth 2.0 providing token-based access control. Each agent should have minimal necessary IAM permissions, following the principle of least privilege. AgentCore's workload identity system enables fine-grained authorization between agents.

Observability becomes critical in multi-agent systems where understanding conversation flows and debugging failures requires tracing requests across agent boundaries. AgentCore integrates with CloudWatch for logs and metrics, and AWS X-Ray for distributed tracing. Session IDs propagated through OpenTelemetry baggage enable correlation of telemetry across agent invocations.

Cost optimization in multi-agent architectures benefits from AgentCore's automatic scaling capabilities. Agents scale independently based on their invocation patterns, ensuring resources match demand. Consider implementing intelligent routing patterns where simpler queries go to lightweight agents, with complex reasoning tasks delegated to more capable (and potentially more expensive) models.

Agent versioning and updates present unique challenges in collaborative systems. A2A's Agent Card mechanism helps by providing version information and capability descriptions that agents can use to adapt their communication patterns. Design agents with graceful degradation in mind, allowing systems to continue functioning even when specific agents are unavailable or undergoing updates.

## The Road Ahead

Amazon Bedrock AgentCore's support for the A2A protocol represents more than a feature addition; it signals the maturation of multi-agent systems from research concept to production reality. By combining A2A's standardized communication with AgentCore's managed infrastructure, organizations can now build sophisticated agent ecosystems that span frameworks, clouds, and domains.

As the A2A protocol evolves under the Linux Foundation's governance, expect expanded modality support, enhanced security features, and richer agent capability descriptions. The growing ecosystem of A2A-compatible tools, frameworks, and platforms will unlock new collaboration patterns and integration possibilities.

For developers building the next generation of AI applications, A2A-enabled multi-agent systems offer a path to handling complexity through specialization and coordination. Each agent becomes an expert in its domain, while the standardized protocol enables them to work together seamlessly. Amazon Bedrock AgentCore provides the foundation to deploy these systems with the reliability, security, and scale that enterprise applications demand.

The future of AI is collaborative, and with A2A support in Amazon Bedrock AgentCore Runtime, that future is available today.

## Getting Started

Ready to build your first A2A-enabled multi-agent system on Amazon Bedrock AgentCore? Visit the Amazon Bedrock AgentCore documentation for detailed tutorials, sample applications, and deployment guides. The monitoring agent example discussed in this post is available in the Amazon Bedrock AgentCore samples repository, providing a complete reference implementation you can adapt for your use cases.

Join the growing community of developers building standardized, interoperable agent systems. Share your experiences, contribute to the A2A specification, and help shape the future of multi-agent AI applications.
