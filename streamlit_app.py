#!/usr/bin/env python3
"""
A2A Multi-Agent Host Orchestrator Chat - Streamlit App

Interactive chat interface for communicating with the Host Orchestrator Agent
which coordinates between monitoring and operations agents using A2A protocol.
"""

import streamlit as st
import json
import asyncio
import sys
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote
import boto3
import requests
import yaml

# Page configuration
st.set_page_config(
    page_title="A2A Host Agent Chat",
    layout="wide",
    initial_sidebar_state="expanded"
)

def load_custom_css():
    """Load custom CSS for better styling"""
    st.markdown("""
    <style>
    .main-header {
        text-align: center;
        padding: 2rem 0;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-radius: 10px;
        margin-bottom: 2rem;
    }
    
    .chat-message {
        padding: 1rem;
        margin: 0.5rem 0;
        border-radius: 10px;
    }
    
    .user-message {
        background: #e3f2fd;
        border-left: 4px solid #2196f3;
    }
    
    .agent-message {
        background: #f3e5f5;
        border-left: 4px solid #9c27b0;
    }
    
    .status-card {
        background: white;
        padding: 1rem;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        text-align: center;
    }
    </style>
    """, unsafe_allow_html=True)

def initialize_session_state():
    """Initialize Streamlit session state"""
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    if 'host_config' not in st.session_state:
        st.session_state.host_config = None

def load_host_config():
    """Load host agent configuration from config.yaml"""
    if st.session_state.host_config is None:
        try:
            config_path = Path(__file__).parent / "multi-agents" / "host" / "config.yaml"
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
                # Get the first host agent config
                host_agents = config.get("host-agent", [])
                if host_agents:
                    st.session_state.host_config = host_agents[0]
                    return st.session_state.host_config
        except Exception as e:
            st.error(f"Failed to load host config: {str(e)}")
            return None
    return st.session_state.host_config

def fetch_ssm_parameter(parameter_path: str, region: str) -> dict:
    """Fetch IDP configuration from SSM Parameter Store."""
    ssm = boto3.client("ssm", region_name=region)
    response = ssm.get_parameter(Name=parameter_path, WithDecryption=True)
    config_str = response["Parameter"]["Value"]
    return json.loads(config_str)

async def get_bearer_token(idp_config: dict) -> str:
    """Get OAuth bearer token using client credentials flow."""
    import httpx
    
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

async def send_message_to_host(message: str):
    """Send message to host agent and return response"""
    host_config = load_host_config()
    if not host_config:
        return "Error: Host configuration not found"
    
    try:
        runtime_arn = host_config["runtime_arn"]
        region = host_config["region"]
        ssm_path = host_config["ssm_idp_config_path"]
        
        # Fetch IDP config and get bearer token
        idp_config = fetch_ssm_parameter(ssm_path, region)
        bearer_token = await get_bearer_token(idp_config)
        
        session_id = str(uuid4())
        
        headers = {
            'Authorization': f'Bearer {bearer_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': session_id
        }
        
        payload = {"prompt": message}
        
        escaped_agent_arn = quote(runtime_arn, safe='')
        
        response = requests.post(
            f'https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_agent_arn}/invocations',
            headers=headers,
            data=json.dumps(payload),
            stream=True
        )
        
        if response.status_code != 200:
            return f"Error: HTTP {response.status_code}"
        
        # Parse streaming response
        response_parts = []
        transfer_agent = None
        final_text = None
        
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith('data: '):
                data = line[6:]
                try:
                    parsed = json.loads(data)
                    
                    if isinstance(parsed, dict):
                        # Check for function call (transfer)
                        if 'content' in parsed and 'parts' in parsed['content']:
                            for part in parsed['content']['parts']:
                                if 'function_call' in part and part['function_call']:
                                    func_call = part['function_call']
                                    if func_call.get('name') == 'transfer_to_agent':
                                        transfer_agent = func_call.get('args', {}).get('agent_name')
                                
                                # Get final text response
                                if 'text' in part and part['text'] and len(part['text']) > 50:
                                    final_text = part['text']
                        
                        # Check actions for transfer info
                        if 'actions' in parsed and parsed['actions'].get('transfer_to_agent'):
                            transfer_agent = parsed['actions']['transfer_to_agent']
                        
                        # Collect text parts
                        if 'text' in parsed and parsed['text']:
                            response_parts.append(parsed['text'])
                    
                    elif isinstance(parsed, str):
                        response_parts.append(parsed)
                        
                except json.JSONDecodeError:
                    if data.strip():
                        response_parts.append(data)
        
        # Build formatted response
        formatted_response = ""
        
        if transfer_agent:
            formatted_response += f"🔄 **Request transferred to {transfer_agent.replace('_', ' ').title()}**\n\n"
        
        if final_text:
            formatted_response += final_text
        elif response_parts:
            clean_text = ''.join(response_parts).strip()
            if clean_text:
                formatted_response += clean_text
            else:
                formatted_response += "Processing request..."
        else:
            formatted_response += "No response received"
        
        return formatted_response
        
    except Exception as e:
        return f"Error: {str(e)}"

def main():
    """Main Streamlit app"""
    load_custom_css()
    initialize_session_state()
    
    # Main header
    st.markdown("""
    <div class="main-header">
        <h1>🤖 A2A Host Agent Chat</h1>
        <p>Chat with the Host Orchestrator Agent</p>
    </div>
    """, unsafe_allow_html=True)
    
    # Sidebar with host agent info
    with st.sidebar:
        st.title("🏠 Host Agent")
        
        host_config = load_host_config()
        if host_config:
            st.success("✅ Host Agent Connected")
            st.markdown(f"**Name:** {host_config['name']}")
            st.markdown(f"**Region:** {host_config['region']}")
            st.markdown(f"**Runtime:** `{host_config['runtime_arn'].split('/')[-1]}`")
        else:
            st.error("❌ Host Agent Not Found")
            st.markdown("Please check config.yaml file")
        
        st.divider()
        
        # Chat statistics
        st.subheader("📊 Chat Stats")
        total_messages = len(st.session_state.chat_history)
        user_messages = len([m for m in st.session_state.chat_history if m["type"] == "user"])
        agent_messages = len([m for m in st.session_state.chat_history if m["type"] == "agent"])
        
        st.metric("Total Messages", total_messages)
        st.metric("Your Messages", user_messages)
        st.metric("Agent Responses", agent_messages)
        
        if st.button("🗑️ Clear Chat"):
            st.session_state.chat_history = []
            st.rerun()
    
    # Main chat interface
    st.header("💬 Chat with Host Agent")
    
    # Display chat history
    chat_container = st.container(height=400)
    with chat_container:
        for message in st.session_state.chat_history:
            if message["type"] == "user":
                st.markdown(f"""
                <div class="chat-message user-message">
                    <strong>👤 You</strong><br>
                    <small>{message['timestamp'].strftime('%H:%M:%S')}</small><br>
                    {message['content']}
                </div>
                """, unsafe_allow_html=True)
            
            elif message["type"] == "agent":
                # Clean up HTML tags and escape remaining HTML
                import re
                clean_content = re.sub(r'<[^>]+>', '', message['content'])  # Remove HTML tags
                clean_content = clean_content.replace('&lt;', '<').replace('&gt;', '>')  # Unescape
                clean_content = clean_content.replace('<', '&lt;').replace('>', '&gt;')  # Re-escape for display
                st.markdown(f"""
                <div class="chat-message agent-message">
                    <strong>🤖 Host Agent</strong><br>
                    <small>{message['timestamp'].strftime('%H:%M:%S')}</small><br>
                    {clean_content}
                </div>
                """, unsafe_allow_html=True)
    
    # Example prompts
    st.markdown("**💡 Example prompts:**")
    col_ex1, col_ex2 = st.columns(2)
    
    with col_ex1:
        if st.button("Check CloudWatch logs for Lambda", use_container_width=True):
            st.session_state.example_message = "Fetch recent cloudwatch logs for lambda functions in my AWS account"
    
    with col_ex2:
        if st.button("Search for EC2 best practices", use_container_width=True):
            st.session_state.example_message = "Search for best practices for managing EC2 instance utilization"
    
    # Message input
    with st.form("chat_form", clear_on_submit=True):
        col1, col2 = st.columns([4, 1])
        
        # Use example message if set
        default_message = getattr(st.session_state, 'example_message', '')
        if default_message:
            st.session_state.example_message = ''  # Clear after use
        
        with col1:
            user_message = st.text_input(
                "Message:",
                value=default_message,
                placeholder="Ask the host agent anything...",
                label_visibility="collapsed"
            )
        
        with col2:
            send_button = st.form_submit_button("📤 Send", use_container_width=True)
        
    if send_button and user_message:
        # Add user message to history
        st.session_state.chat_history.append({
            "type": "user",
            "timestamp": datetime.now(),
            "content": user_message
        })
        
        # Send to host agent
        with st.spinner("🤖 Host agent is thinking..."):
            try:
                response = asyncio.run(send_message_to_host(user_message))
                
                # Add agent response to history
                st.session_state.chat_history.append({
                    "type": "agent",
                    "timestamp": datetime.now(),
                    "content": response
                })
                
                st.rerun()
                
            except Exception as e:
                st.error(f"Error: {str(e)}")
                
                # Add error to history
                st.session_state.chat_history.append({
                    "type": "agent",
                    "timestamp": datetime.now(),
                    "content": f"Error: {str(e)}"
                })
                
                st.rerun()

if __name__ == "__main__":
    main()