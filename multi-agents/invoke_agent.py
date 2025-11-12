import boto3
import json
from uuid import uuid4

def invoke_agent(agent_arn: str, prompt: str, session_id: str = None):
    """Invoke agent runtime with a prompt"""
    if not session_id:
        session_id = str(uuid4())
    
    # Initialize the Bedrock AgentCore client
    agent_core_client = boto3.client('bedrock-agentcore')
    
    # Prepare the payload
    payload = json.dumps({"prompt": prompt}).encode()
    
    # Invoke the agent
    response = agent_core_client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=session_id,
        payload=payload
    )
    
    # Process and print the response
    if "text/event-stream" in response.get("contentType", ""):
        # Handle streaming response
        content = []
        for line in response["response"].iter_lines(chunk_size=10):
            if line:
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    line = line[6:]
                    print(line)
                    content.append(line)
        print("\nComplete response:", "\n".join(content))
        return "\n".join(content)
    
    elif response.get("contentType") == "application/json":
        # Handle standard JSON response
        content = []
        for chunk in response.get("response", []):
            content.append(chunk.decode('utf-8'))
        result = json.loads(''.join(content))
        print(result)
        return result
    
    else:
        # Print raw response for other content types
        print(response)
        return response

def main():
    """Main function to test agent invocation"""
    # Example agent ARNs - replace with actual values
    monitoring_agent_arn = "arn:aws:bedrock-agentcore:us-east-1:206409480438:runtime/monitoring_agent-Af2IjZ4hhb"
    ops_agent_arn = "arn:aws:bedrock-agentcore:us-east-1:206409480438:runtime/ops_remediation_agent-lSiAaCBUf3"
    
    # Test monitoring agent
    # print("Testing Monitoring Agent:")
    # result = invoke_agent(monitoring_agent_arn, "Check CloudWatch logs for errors")
    # print(f"Result: {result}\n")
    
    # Test ops agent
    print("Testing Ops Remediation Agent:")
    result = invoke_agent(ops_agent_arn, "Search for DynamoDB best practices")
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
