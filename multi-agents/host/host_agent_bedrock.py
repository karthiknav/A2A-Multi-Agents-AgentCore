import logging
from dotenv import load_dotenv
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types
from bedrock_agentcore import BedrockAgentCoreApp
import os
import yaml
import boto3
from botocore.exceptions import ClientError
from pathlib import Path
# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Load config from config.yaml relative to this file
config_path = Path(__file__).parent / "config.yaml"
with open(config_path, 'r') as f:
    config_data = yaml.safe_load(f)

APP_NAME = "HostAgentA2A"

app = BedrockAgentCoreApp()

session_service = InMemorySessionService()

root_agent = None


@app.entrypoint
async def call_agent(payload: dict, context):
    global root_agent

    session_id = context.session_id
    logger.info(f"Received request with session_id: {session_id}")

    # actor_id = request_headers["x-amzn-bedrock-agentCore-runtime-custom-actor"]

    # if not actor_id:
    #     raise Exception("Actor id is not is not set")
    # TODO: Actor Id
    # Ensure session exists before running
    actor_id = "Actor1"

    if not session_id:
        raise Exception("Context session_id is not set")

    if not root_agent:
        # Import agent creation inside entrypoint so workload identity is available
        from agent import get_agent_and_card

        logger.info("Initializing root agent and resolving agent cards...")
        # Create root agent once - LazyClientFactory creates fresh httpx clients
        # on each A2A invocation in the current event loop context
        try:
            root_agent, agents_cards = await get_agent_and_card(
                session_id=session_id, actor_id=actor_id
            )
            logger.info(
                f"Successfully initialized root agent. Agent cards: {list(agents_cards.keys())}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize root agent: {e}", exc_info=True)
            raise

        yield agents_cards

    query = payload.get("prompt")
    logger.info(f"Processing query: {query}")

    if not query:
        raise KeyError("'prompt' field is required in payload")

    in_memory_session = session_service.get_session_sync(
        app_name=APP_NAME, user_id=actor_id, session_id=session_id
    )

    if not in_memory_session:
        # Session doesn't exist, create it
        _ = session_service.create_session_sync(
            app_name=APP_NAME, user_id=actor_id, session_id=session_id
        )

    runner = Runner(
        agent=root_agent, app_name=APP_NAME, session_service=session_service
    )

    content = types.Content(role="user", parts=[types.Part(text=query)])

    # Use async run to properly maintain event loop across invocations
    async for event in runner.run_async(
        user_id=actor_id, session_id=session_id, new_message=content
    ):
        yield event

def load_api_keys_from_ssm(config_data: dict) -> dict:
    """
    Load API keys from SSM Parameter Store based on config.
    Falls back to environment variables if SSM retrieval fails.

    Args:
        config_data: Configuration dictionary containing SSM parameter paths

    Returns:
        Dictionary with API keys: {'TAVILY_API_KEY': '...', 'OPENAI_API_KEY': '...', ...}
    """
    api_keys = {}
    ssm_config = config_data.get('ssm_parameters', {})
    region = ssm_config.get('region', 'us-west-2')
    parameters = ssm_config.get('parameters', {})

    # Map of environment variable names to SSM parameter keys
    key_mapping = {
        'GOOGLE_API_KEY': 'google_api_key'
    }

    for env_var, param_key in key_mapping.items():
        try:
            # Try SSM first
            param_path = parameters.get(param_key)
            if param_path:
                logger.info(f"Retrieving {env_var} from SSM: {param_path}")
                api_keys[env_var] = get_parameter_from_ssm(param_path, region)
                logger.info(f"✅ Successfully retrieved {env_var} from SSM")
            else:
                # Fall back to environment variable
                logger.warning(f"No SSM parameter configured for {env_var}, checking environment variables")
                value = os.getenv(env_var)
                if value:
                    api_keys[env_var] = value
                    logger.info(f"✅ Loaded {env_var} from environment variable")
                else:
                    logger.error(f"❌ {env_var} not found in SSM or environment variables")
        except Exception as e:
            # Fall back to environment variable on any error
            logger.warning(f"Failed to retrieve {env_var} from SSM: {e}. Falling back to environment variable.")
            value = os.getenv(env_var)
            if value:
                api_keys[env_var] = value
                logger.info(f"✅ Loaded {env_var} from environment variable (fallback)")
            else:
                logger.error(f"❌ {env_var} not found in SSM or environment variables")

    return api_keys
def get_parameter_from_ssm(
    parameter_name: str,
    region_name: str = "us-west-2",
    decrypt: bool = True
) -> str:
    """
    Retrieve parameter from AWS Systems Manager Parameter Store.

    Args:
        parameter_name: Name of the SSM parameter (e.g., '/ops-orchestrator/tavily-api-key')
        region_name: AWS region where the parameter is stored
        decrypt: Whether to decrypt SecureString parameters

    Returns:
        Parameter value as string

    Raises:
        ClientError: If parameter not found or access denied
    """
    ssm_client = boto3.client('ssm', region_name=region_name)
    try:
        response = ssm_client.get_parameter(
            Name=parameter_name,
            WithDecryption=decrypt
        )
        return response['Parameter']['Value']
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ParameterNotFound':
            logger.error(f"SSM parameter '{parameter_name}' not found in region {region_name}")
        elif error_code == 'AccessDeniedException':
            logger.error(f"Access denied to SSM parameter '{parameter_name}'. Check IAM permissions.")
        else:
            logger.error(f"Error retrieving SSM parameter '{parameter_name}': {e}")
        raise

def initialize_api_keys():
    """Initialize API keys from SSM Parameter Store"""
    print("\n" + "="*80)
    print("Loading API Keys from AWS Systems Manager Parameter Store")
    print("="*80)

    api_keys = load_api_keys_from_ssm(config_data)

    # Set global variables for API keys
    GOOGLE_API_KEY = api_keys.get('GOOGLE_API_KEY')

    # Set environment variables for compatibility with libraries that read from os.environ
    if GOOGLE_API_KEY:
        os.environ['GOOGLE_API_KEY'] = GOOGLE_API_KEY

    print("\nAPI Keys Status:")
    print(f"GOOGLE_API_KEY: {'✅ Loaded' if GOOGLE_API_KEY else '❌ Not Found'}")
    print("="*80 + "\n")
    
    return api_keys

if __name__ == "__main__":
    initialize_api_keys()
    app.run()  # Ready to run on Bedrock AgentCore