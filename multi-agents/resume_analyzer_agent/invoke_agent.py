import boto3
import json

agent_arn = "arn:aws:bedrock-agentcore:us-east-1:206409480438:runtime/agent-fqNLYLGAR4"
agentcore_client = boto3.client(
    'bedrock-agentcore',
    region_name="us-east-1"
)

# Resume analyzer payload
payload = {
    "bucket": "amzn-s3-resume-analyzer-bucket",
    "resume_key": "john_smith_resume.txt",
    "job_description_key": "job_description.txt"
}

print("Invoking resume analyzer agent...")
print(f"Payload: {json.dumps(payload, indent=2)}")

boto3_response = agentcore_client.invoke_agent_runtime(
    agentRuntimeArn=agent_arn,
    qualifier="DEFAULT",
    payload=json.dumps(payload)
)
if "text/event-stream" in boto3_response.get("contentType", ""):
    content = []
    for line in boto3_response["response"].iter_lines(chunk_size=1):
        if line:
            line = line.decode("utf-8")
            if line.startswith("data: "):
                line = line[6:]
                print(line)
                content.append(line)
    print("\nFinal content:")
    print("\n".join(content))
else:
    try:
        events = []
        for event in boto3_response.get("response", []):
            events.append(event)
        print("Response events:")
        for event in events:
            print(json.dumps(json.loads(event.decode("utf-8")), indent=2))
    except Exception as e:
        print(f"Error reading response: {e}")
        print(f"Raw response: {boto3_response}")