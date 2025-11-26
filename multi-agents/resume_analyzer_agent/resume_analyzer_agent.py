import asyncio
import json
import logging
import os
import time
import hashlib
from typing import Dict, Any
import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent, tool
import PyPDF2
from docx import Document
from pathlib import Path
from strands.hooks import AgentInitializedEvent, HookProvider, HookRegistry, MessageAddedEvent

# Import memory management modules
from bedrock_agentcore_starter_toolkit.operations.memory.manager import MemoryManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole
from bedrock_agentcore.memory.session import MemorySession, MemorySessionManager

# Define message role constants
USER = MessageRole.USER
ASSISTANT = MessageRole.ASSISTANT

# Configuration
REGION = os.getenv('AWS_REGION', 'us-east-1') # AWS region for the agent
ACTOR_ID = "user_123" # It can be any unique identifier (AgentID, User ID, etc.)
SESSION_ID = "personal_session_001" # Unique session identifier

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize AWS clients
s3_client = boto3.client('s3')

# Environment variables
MODEL_ID = os.environ.get('MODEL_ID', 'us.anthropic.claude-3-5-sonnet-20241022-v2:0')
DOCUMENTS_BUCKET = os.environ.get('DOCUMENTS_BUCKET', 'hr-agents-documents-agentcore')
# Initialize AgentCore app
app = BedrockAgentCoreApp()

# Initialize Memory Manager 
memory_manager = MemoryManager(region_name=REGION)
memory_name = "ResumeAnalyzerMemoryManager"

logger.info(f"✅ MemoryManager initialized for region: {REGION}")
logger.info(f"Memory manager type: {type(memory_manager)}")

# Create memory resource using MemoryManager
logger.info(f"Creating memory '{memory_name}' for short-term conversational storage...")

try:
    memory = memory_manager.get_or_create_memory(
        name=memory_name,
        strategies=[],  # No strategies for short-term memory
        description="Short-term memory for resume analyzer",
        event_expiry_days=7,  # Retention period for short-term memory
        memory_execution_role_arn=None,  # Optional for short-term memory
    )
    memory_id = memory.id
    logger.info(f"✅ Successfully created/retrieved memory with MemoryManager:")
    logger.info(f"   Memory ID: {memory_id}")
    logger.info(f"   Memory Name: {memory.name}")
    logger.info(f"   Memory Status: {memory.status}")
    
except Exception as e:
    # Handle any errors during memory creation with enhanced error reporting
    logger.error(f"❌ Memory creation failed: {e}")
    logger.error(f"Error type: {type(e).__name__}")
    import traceback
    traceback.print_exc()
    
    # Cleanup on error - delete the memory if it was partially created
    if 'memory_id' in locals():
        try:
            logger.info(f"Attempting cleanup of partially created memory: {memory_id}")
            memory_manager.delete_memory(memory_id)
            logger.info(f"✅ Successfully cleaned up memory: {memory_id}")
        except Exception as cleanup_error:
            logger.error(f"❌ Failed to clean up memory: {cleanup_error}")
    
    # Re-raise the original exception
    raise

# Initialize the session memory manager
session_manager = MemorySessionManager(memory_id=memory.id, region_name=REGION)

# Global session tracking
current_session = None
current_session_id = None

logger.info(f"✅ Session manager initialized for memory: {memory.id}")

class MemoryHookProvider(HookProvider):
    def __init__(self, memory_session: MemorySession):  # Accept MemorySession instead
        self.memory_session = memory_session
    
    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts using MemorySession"""
        try:
            # Use the pre-configured memory session (no need for actor_id/session_id)
            recent_turns = self.memory_session.get_last_k_turns(k=5)
            
            if recent_turns:
                # Format conversation history for context
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        # Handle both EventMessage objects and dict formats
                        if hasattr(message, 'role') and hasattr(message, 'content'):
                            role = message['role']
                            content = message['content']
                        else:
                            role = message.get('role', 'unknown')
                            content = message.get('content', {}).get('text', '')
                        context_messages.append(f"{role}: {content}")
                
                context = "\n".join(context_messages)
                # Add context to agent's system prompt
                event.agent.system_prompt += f"\n\nRecent conversation:\n{context}"
                logger.info(f"✅ Loaded {len(recent_turns)} conversation turns using MemorySession")
                
        except Exception as e:
            logger.error(f"Memory load error: {e}")

    def on_message_added(self, event: MessageAddedEvent):
        """Store messages in memory using MemorySession"""
        messages = event.agent.messages
        try:
            if messages and len(messages) > 0 and messages[-1]["content"][0].get("text"):
                message_text = messages[-1]["content"][0]["text"]
                message_role = MessageRole.USER if messages[-1]["role"] == "user" else MessageRole.ASSISTANT
                
                # Use memory session instance (no need to pass actor_id/session_id)
                result = self.memory_session.add_turns(
                    messages=[ConversationalMessage(message_text, message_role)]
                )
                
                event_id = result['eventId']
                logger.info(f"✅ Stored message with Event ID: {event_id}, Role: {message_role.value}")
                
        except Exception as e:
            logger.error(f"Memory save error: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
    
    def register_hooks(self, registry: HookRegistry):
        # Register memory hooks
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        logger.info("✅ Memory hooks registered with MemorySession")

def get_or_create_session(resume_key: str = None, job_description_key: str = None):
    """Get existing session or create new one based on documents"""
    global current_session, current_session_id
    
    if resume_key:
        # New document upload - create new session
        session_data = f"{resume_key}_{job_description_key or 'no_job'}"
        session_id = hashlib.md5(session_data.encode()).hexdigest()[:16]
        
        if session_id != current_session_id:
            current_session = session_manager.create_memory_session(
                actor_id=ACTOR_ID,
                session_id=session_id
            )
            current_session_id = session_id
            logger.info(f"✅ Created new session: {session_id}")
    
    return current_session

async def process_query_with_strands_agents(query: str):
    """Process plain text queries using Strands agents with memory context"""
    try:
        session = get_or_create_session()
        memory_hook_provider = MemoryHookProvider(session)
        
        agent = Agent(
            model=MODEL_ID,
            system_prompt="""You are an expert HR resume analyzer with access to previous conversations 
            about specific resume and job combinations. Use context to provide relevant responses.""",
            hooks=[memory_hook_provider]
        )
        
        return agent.stream_async(query)
        
    except Exception as e:
        logger.error(f"❌ Error in query processing: {str(e)}")
        raise

@app.entrypoint
async def invoke(payload):
    """AgentCore entrypoint for HR resume evaluation"""
    try:
        logger.info(f"🚀 Starting HR Agent invocation")
        logger.info(f"📥 Received payload: {json.dumps(payload, indent=2)}")
        
        # Check if this is a plain text query (follow-up question) or S3 document processing
        if 'query' in payload or 'message' in payload:
            # Handle plain text queries/follow-up questions
            query = payload.get('query') or payload.get('message', '')
            logger.info(f"💬 Processing follow-up query: {query}")
            agent_stream = await process_query_with_strands_agents(query)
        else:
            # Handle S3 document processing (original logic)
            bucket = payload.get('bucket', DOCUMENTS_BUCKET)
            resume_key = payload.get('resume_key')
            job_description_key = payload.get('job_description_key')
            
            logger.info(f"📂 Using bucket: {bucket}")
            logger.info(f"📄 Resume key: {resume_key}")
            logger.info(f"📋 Job description key: {job_description_key}")
            
            # Create/update session based on documents
            get_or_create_session(resume_key, job_description_key)
            
            if not resume_key:
                logger.error("❌ Missing resume_key in payload")
                raise ValueError("resume_key is required in payload")
            
            logger.info("🔄 Starting resume processing with Strands agents")
            agent_stream = await process_resume_with_strands_agents(bucket, resume_key, job_description_key)
        tool_name = None
        event_count = 0
        
        try:
            async for event in agent_stream:
                event_count += 1
                logger.debug(f"📊 Processing event #{event_count}: {type(event)}")

                if (
                    "current_tool_use" in event
                    and event["current_tool_use"].get("name") != tool_name
                ):
                    tool_name = event["current_tool_use"]["name"]
                    logger.info(f"🔧 Agent using tool: {tool_name}")
                    yield f"\n\n🔧 Using tool: {tool_name}\n\n"

                if "data" in event:
                    tool_name = None
                    data_length = len(str(event["data"]))
                    logger.debug(f"📤 Yielding data chunk of {data_length} characters")
                    yield event["data"]
                    
        except Exception as e:
            logger.error(f"❌ Error in agent stream processing: {str(e)}")
            yield f"Error: {str(e)}"
            
        logger.info(f"✅ Completed processing {event_count} events")
        
    except Exception as e:
        logger.error(f"❌ Error in agent stream processing: {str(e)}")
        yield f"Error: {str(e)}"

async def process_resume_with_strands_agents(bucket: str, resume_key: str, job_description_key: str) -> Dict[str, Any]:
    """Process resume using Strands multi-agent collaboration"""
    try:
        logger.info(f"📥 Downloading resume from s3://{bucket}/{resume_key}")
        # Download resume content from S3
        resume_content = download_s3_file(bucket, resume_key)
        logger.info(f"✅ Resume downloaded, length: {len(resume_content)} characters")
        
        # Download job description content from S3
        if job_description_key:
            logger.info(f"📥 Downloading job description from s3://{bucket}/{job_description_key}")
            job_content = download_s3_file(bucket, job_description_key)
            logger.info(f"✅ Job description downloaded, length: {len(job_content)} characters")
        else:
            logger.info("ℹ️ No job description provided, using default")
            job_content = "No specific job description provided."
        
        logger.info("🤖 Creating HR Supervisor agent")
        # Create the HR Supervisor agent
        supervisor_agent = create_supervisor_agent()
        logger.info("✅ HR Supervisor agent created successfully")
        
        # Create evaluation request
        evaluation_request = f"""
        Please evaluate this candidate for the position using your specialized agent team.
        
        RESUME:
        {resume_content}
        
        JOB DESCRIPTION:
        {job_content}

        Work with your team to provide a comprehensive evaluation. Coordinate with:
        1. ResumeParserAgent to extract structured information
        2. JobAnalyzerAgent to analyze job requirements
        3. ResumeEvaluatorAgent to evaluate candidate fit
        4. GapIdentifierAgent to identify missing qualifications
        5. CandidateRaterAgent to provide numerical rating

        Provide your final response as a comprehensive markdown format.
        """
        
        # Execute evaluation
        logger.info("🚀 Starting AgentCore multi-agent evaluation...")
        logger.info(f"📝 Evaluation request length: {len(evaluation_request)} characters")
        agent_stream = supervisor_agent.stream_async(evaluation_request)
        logger.info("✅ Agent stream initialized successfully")
        return agent_stream
        
    except Exception as e:
        logger.error(f"❌ Error in resume processing: {str(e)}")
        logger.error(f"🔍 Error details: {type(e).__name__}")
        raise

def create_supervisor_agent():
    """Create the HR Supervisor agent with specialized tools"""
    session = get_or_create_session()
    memory_hook_provider = MemoryHookProvider(session)
    
    @tool
    def extract_resume_info(resume_text: str) -> str:
        """Extract structured information from resume text"""
        parser_agent = Agent(
            model=MODEL_ID,
            system_prompt="""You are a Resume Parser Agent specializing in extracting structured information from resumes.

Extract the following information:
1. Personal Information (name, contact, title, URLs)
2. Work Experience (companies, titles, dates, achievements, technologies)
3. Education (degrees, institutions, dates, coursework)
4. Skills (technical, domain, soft skills, languages, proficiency)
5. Projects (names, descriptions, technologies, outcomes)

Structure your response as a JSON object with these categories."""
        )
        
        result = parser_agent(resume_text)
        return safe_extract_content(result)
    
    @tool
    def analyze_job_requirements(job_description: str) -> str:
        """Analyze job requirements"""
        analyzer_agent = Agent(
            model=MODEL_ID,
            system_prompt="""You are a Job Analyzer Agent specializing in extracting job requirements.

Analyze and extract:
1. Required Qualifications (education, experience, skills, certifications)
2. Preferred Qualifications (additional beneficial skills)
3. Skills (technical, domain, soft skills, languages, proficiency, priority)
4. Company Culture (environment, values, work style)
5. Compensation and Benefits (if provided)

Structure your response as a JSON object with these categories."""
        )
        
        result = analyzer_agent(job_description)
        return safe_extract_content(result)
    
    @tool
    def evaluate_candidate_fit(resume_info: str, job_requirements: str) -> str:
        """Evaluate candidate fit"""
        evaluator_agent = Agent(
            model=MODEL_ID,
            system_prompt="""You are a Resume Evaluator Agent specializing in comparing candidates against job requirements.

Evaluate:
1. Skills Match Analysis (technical alignment, proficiency, missing skills)
2. Experience Relevance Assessment (industry, role similarity, years)
3. Education Fit Evaluation (degree requirements, certifications)
4. Project Relevance Review (scale, technology alignment)

Structure your response as a JSON object with detailed analysis."""
        )
        
        evaluation_request = f"RESUME INFO:\n{resume_info}\n\nJOB REQUIREMENTS:\n{job_requirements}"
        result = evaluator_agent(evaluation_request)
        return safe_extract_content(result)

    @tool
    def identify_gaps(resume_info: str, job_requirements: str) -> str:
        """Identify gaps and inconsistencies"""
        gap_agent = Agent(
            model=MODEL_ID,
            system_prompt="""You are a Gap Identifier Agent specializing in finding gaps and inconsistencies.

Identify:
1. Missing Qualifications (required skills, education, experience)
2. Experience Gaps (timeline gaps, missing industry experience)
3. Skill Mismatches (core skills, domain/functional skills)
4. Areas Needing Clarification (vague accomplishments, unclear levels)

Structure your response as a JSON object with specific examples."""
        )
        
        gap_request = f"RESUME INFO:\n{resume_info}\n\nJOB REQUIREMENTS:\n{job_requirements}"
        result = gap_agent(gap_request)
        return safe_extract_content(result)

    @tool
    def rate_candidate(resume_info: str, job_requirements: str, evaluation_info: str) -> str:
        """Rate candidate on 1-5 scale"""
        rater_agent = Agent(
            model=MODEL_ID,
            system_prompt="""You are a Candidate Rater Agent specializing in scoring candidates on a 1-5 scale.

Provide:
1. Overall Fit Score (1-5 scale with clear criteria)
2. Detailed Justification (evidence-based reasoning)
3. Strengths (key qualifications and achievements)
4. Weaknesses (missing qualifications and gaps)
5. Risk Assessment (likelihood of success, challenges)

Structure your response as a JSON object with numerical rating and analysis."""
        )
        
        rating_request = f"RESUME INFO:\n{resume_info}\n\nJOB REQUIREMENTS:\n{job_requirements}\n\nEVALUATION:\n{evaluation_info}"
        result = rater_agent(rating_request)
        return safe_extract_content(result)


    # Create the main HR Supervisor Agent
    supervisor_agent = Agent(
        model=MODEL_ID,
        hooks= [memory_hook_provider],
        tools=[
            extract_resume_info,
            analyze_job_requirements, 
            evaluate_candidate_fit,
            identify_gaps,
            rate_candidate
        ],
        system_prompt="""You are the Supervisor Agent for HR resume evaluation running on Amazon Bedrock AgentCore Runtime.

Coordinate with your specialized team to provide comprehensive candidate evaluations:

1. Have ResumeParserAgent extract structured information from the resume
2. Have JobAnalyzerAgent analyze the job requirements
3. Have ResumeEvaluatorAgent evaluate candidate fit
4. Have GapIdentifierAgent identify missing qualifications
5. Have CandidateRaterAgent provide numerical rating (1-5 scale)

CRITICAL: Output your final evaluation in this EXACT Markdown format:

### Candidate Fit Summary

| Suitability | Decision | Seniority | Match Summary | Red Flags | Availability |
|---:|:--:|:--:|:--|:--|:--|
| **{score}%** | **{decision}** | **{seniority}** | Core: **{coreMatch}** • Domain: **{domainMatch}** • Soft: **{softMatch}** | {redFlagsOrDash} | {availability} |

> _Why this score:_ {oneLineRationale}

---

#### Must‑Haves
| Must‑Have | Status | Evidence |
|---|:--:|---|
{mustHaveRows}

---

#### Core Skills (Top 6)
| Skill | JD Priority | Candidate Level | Evidence |
|---|:--:|:--:|---|
{coreSkillRows}

---

#### Domain / Functional (Top 4)
| Domain Skill | JD Priority | Candidate Level | Evidence |
|---|:--:|:--:|---|
{domainSkillRows}

---

#### Evidence Snippets
- {evidence1}
- {evidence2}
- {evidence3}
- {evidence4}

---

#### Gaps & Risks
- {gap1}
- {gap2}
- {risk1}

**Recommendation:** {oneLineRecommendation}
""")
    
    return supervisor_agent

def safe_extract_content(result) -> str:
    """Extract text content from Strands agent response"""
    try:
        # Handle different response formats
        if hasattr(result, 'content') and isinstance(result.content, list):
            text_parts = []
            for item in result.content:
                if hasattr(item, 'text'):
                    text_parts.append(item.text)
                elif isinstance(item, dict) and 'text' in item:
                    text_parts.append(item['text'])
                else:
                    text_parts.append(str(item))
            return '\n'.join(text_parts)
        elif hasattr(result, 'content'):
            return str(result.content)
        elif hasattr(result, 'message'):
            return str(result.message)
        elif isinstance(result, dict):
            # Handle dict response format from AgentCore
            if 'role' in result and 'content' in result:
                content = result['content']
                if isinstance(content, list):
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict) and 'text' in item:
                            text_parts.append(item['text'])
                        else:
                            text_parts.append(str(item))
                    return '\n'.join(text_parts)
                else:
                    return str(content)
            # Handle message format
            elif 'message' in result:
                message = result['message']
                if isinstance(message, dict) and 'content' in message:
                    content = message['content']
                    if isinstance(content, list):
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict) and 'text' in item:
                                text_parts.append(item['text'])
                            else:
                                text_parts.append(str(item))
                        return '\n'.join(text_parts)
                    else:
                        return str(content)
                else:
                    return str(message)
            # Handle direct text content
            elif isinstance(result, str):
                return result
            return str(result)
        else:
            return str(result)
    except Exception as e:
        logger.error(f"Error extracting content: {str(e)}")
        logger.debug(f"Result type: {type(result)}, Result: {str(result)[:500]}")
        return str(result)

def parse_evaluation_result(evaluation_result, resume_key: str, resume_content: str) -> Dict[str, Any]:
    """Parse the evaluation result into structured format"""
    try:
        from datetime import datetime

        evaluation_content = safe_extract_content(evaluation_result)
        logger.info(f"🔍 Raw agent response type: {type(evaluation_result)}")
        logger.info(f"🔍 Extracted content length: {len(evaluation_content)} chars")
        logger.info(f"🔍 Content preview: {evaluation_content[:500]}...")
        evaluation_json = safe_parse_json(evaluation_content)

        candidate_name = extract_name_from_key(resume_key)

        if evaluation_json and isinstance(evaluation_json, dict):
            # Extract candidate name from parsed data if available
            if "resume_parsing" in evaluation_json and "personal_info" in evaluation_json["resume_parsing"]:
                parsed_name = evaluation_json["resume_parsing"]["personal_info"].get("name")
                if parsed_name and parsed_name.strip():
                    candidate_name = parsed_name

            # Extract individual rating for easy querying
            rating = None
            if "candidate_rating" in evaluation_json and isinstance(evaluation_json["candidate_rating"], dict):
                rating = evaluation_json["candidate_rating"].get("rating")

            # Build result matching other agents' structure
            result = {
                "name": candidate_name,
                "resume_key": resume_key,
                "status": "completed",
                "created_at": datetime.utcnow().isoformat(),
                "completed_at": datetime.utcnow().isoformat(),
                "evaluated_by": "Strands AgentCore Multi-Agent System",
                "resume_text": resume_content,

                # Store individual components as separate attributes (matching other agents)
                "resume_parsing": evaluation_json.get("resume_parsing", {}),
                "job_analysis": evaluation_json.get("job_analysis", {}),
                "evaluation_results": evaluation_json.get("resume_evaluation", {}),
                "gaps_analysis": evaluation_json.get("gap_analysis", {}),
                "candidate_rating": evaluation_json.get("candidate_rating", {}),
                "interview_notes": evaluation_json.get("interview_notes", {}),

                # Additional fields for compatibility and querying
                "job_title": "AI Engineer",  # Default job title like other agents
                "rating": rating,  # Extract numeric rating for easy filtering

                # Keep minimal metadata for debugging (reduced size)
                "agentcore_metadata": {
                    "runtime_platform": "Amazon Bedrock AgentCore",
                    "agent_framework": "Strands Agents SDK",
                    "model_used": MODEL_ID,
                    "processing_timestamp": datetime.utcnow().isoformat()
                }
            }

            return result
        else:
            # Fallback structure when JSON parsing fails
            logger.warning(f"Could not parse evaluation JSON, storing as fallback structure")
            logger.warning(f"Raw evaluation content (first 1000 chars): {evaluation_content[:1000]}")
            logger.warning(f"Content type: {type(evaluation_content)}")
            return {
                "name": candidate_name,
                "resume_key": resume_key,
                "status": "completed",
                "created_at": datetime.utcnow().isoformat(),
                "evaluated_by": "Strands AgentCore Multi-Agent System (Parsing Issue)",
                "resume_text": resume_content,
                "job_title": "AI Engineer",
                "agentcore_metadata": {
                    "runtime_platform": "Amazon Bedrock AgentCore",
                    "evaluation_content": evaluation_content,  # Store full content for debugging
                    "parsing_failed": True
                }
            }

    except Exception as e:
        logger.error(f"Error parsing evaluation: {str(e)}")
        candidate_name = extract_name_from_key(resume_key)
        return {
            "name": candidate_name,
            "resume_key": resume_key,
            "status": "error",
            "error": f"Parsing error: {str(e)}",
            "created_at": datetime.utcnow().isoformat(),
            "evaluated_by": "Strands AgentCore Multi-Agent System (Error)"
        }

def safe_parse_json(content: str) -> Dict[str, Any]:
    """Safely parse JSON from text content"""
    try:
        content = content.strip()

        logger.info(f"🔍 Parsing JSON from content type: {type(content)}")
        logger.info(f"🔍 Content starts with: {content[:100]}")

        # Handle Python dict string representation: "{'role': 'assistant', 'content': [{'text': 'JSON_HERE'}]}"
        if content.startswith("{'role': 'assistant', 'content': [{'text': '") and content.endswith("'}]}"):
            logger.info("🔍 Detected Python dict string representation")
            # Use regex to extract the JSON from the text field instead of ast.literal_eval
            import re
            try:
                # Find the start of the text content after 'text': '
                text_start_pattern = r"'text':\s*'"
                text_start_match = re.search(text_start_pattern, content)
                if text_start_match:
                    start_pos = text_start_match.end()
                    # Find the end by looking for the closing pattern '}]}
                    # We need to work backwards from the end to handle escaped quotes
                    end_pattern = r"'\s*\}\s*\]\s*\}\s*$"

                    # Search for the end pattern
                    end_match = re.search(end_pattern, content)
                    if end_match:
                        end_pos = end_match.start()
                        # Extract the text content between start and end
                        text_content = content[start_pos:end_pos]

                        # Unescape the content - handle backslash escaping
                        text_content = text_content.replace('\\\\', '\x00')  # Temporarily replace double backslash
                        text_content = text_content.replace("\\'", "'").replace('\\n', '\n').replace('\\"', '"')
                        text_content = text_content.replace('\x00', '\\')  # Restore single backslashes

                        logger.info(f"🔍 Extracted text content length: {len(text_content)}")
                        logger.info(f"🔍 Text content preview: {text_content[:200]}...")

                        # Clean up the text content - remove thinking tags and find JSON
                        if '<thinking>' in text_content and '</thinking>' in text_content:
                            # Remove thinking sections
                            text_content = re.sub(r'<thinking>.*?</thinking>\s*', '', text_content, flags=re.DOTALL)
                            logger.info(f"🔍 Removed thinking tags, new length: {len(text_content)}")
                            logger.info(f"🔍 Content after thinking removal: {text_content[:200]}...")

                        # Clean up the text content more thoroughly
                        text_content = text_content.strip()

                        # Remove any leading/trailing whitespace and newlines
                        while text_content.startswith(('\n', '\r', ' ', '\t')):
                            text_content = text_content[1:]
                        while text_content.endswith(('\n', '\r', ' ', '\t')):
                            text_content = text_content[:-1]

                        logger.info(f"🔍 Cleaned content starts with: '{text_content[:50]}...'")
                        logger.info(f"🔍 Cleaned content ends with: '...{text_content[-50:]}'")

                        # Find the JSON part
                        if text_content.startswith('{') and text_content.endswith('}'):
                            logger.info("🔍 Content looks like JSON, attempting to parse")
                            try:
                                return json.loads(text_content)
                            except json.JSONDecodeError as e:
                                logger.warning(f"🔍 JSON parse failed: {str(e)}")
                                logger.warning(f"🔍 Failed content preview: '{text_content[:100]}...'")

                        # Look for JSON structure in the content
                        start_idx = text_content.find('{')
                        if start_idx != -1:
                            logger.info(f"🔍 Found JSON structure at position {start_idx}")
                            brace_count = 0
                            end_idx = start_idx

                            for i in range(start_idx, len(text_content)):
                                if text_content[i] == '{':
                                    brace_count += 1
                                elif text_content[i] == '}':
                                    brace_count -= 1
                                    if brace_count == 0:
                                        end_idx = i + 1
                                        break

                            if brace_count == 0:
                                json_text = text_content[start_idx:end_idx]
                                logger.info(f"🔍 Extracted JSON substring length: {len(json_text)}")
                                logger.info(f"🔍 JSON substring preview: '{json_text[:100]}...'")
                                try:
                                    return json.loads(json_text)
                                except json.JSONDecodeError as e:
                                    logger.warning(f"🔍 JSON substring parse failed: {str(e)}")

                        # If we get here, try to parse as-is
                        logger.info("🔍 Attempting final direct parse")
                        return json.loads(text_content)
                    else:
                        logger.warning("🔍 Failed to find end pattern in content")
                else:
                    logger.warning("🔍 Failed to find text start pattern in content")
            except Exception as e:
                logger.warning(f"🔍 Failed to parse Python dict with improved regex: {str(e)}")

        # Handle escaped JSON strings first
        if '\\\"' in content:
            content = content.replace('\\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')

        # Try direct JSON parsing first
        if content.startswith('{') and content.endswith('}'):
            logger.info("🔍 Attempting direct JSON parsing")
            return json.loads(content)

        # Look for JSON in markdown code blocks
        import re
        json_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        json_match = re.search(json_pattern, content, re.DOTALL | re.IGNORECASE)

        if json_match:
            logger.info("🔍 Found JSON in markdown code block")
            json_text = json_match.group(1).strip()
            if '\\\"' in json_text:
                json_text = json_text.replace('\\\"', '"').replace('\\n', '\n')
            return json.loads(json_text)

        # Look for JSON structure anywhere in the content
        start_idx = content.find('{')
        if start_idx != -1:
            logger.info(f"🔍 Found JSON structure starting at index {start_idx}")
            brace_count = 0
            end_idx = start_idx

            for i in range(start_idx, len(content)):
                if content[i] == '{':
                    brace_count += 1
                elif content[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break

            if brace_count == 0:
                json_text = content[start_idx:end_idx]
                logger.info(f"🔍 Extracted JSON text length: {len(json_text)}")
                if '\\\"' in json_text:
                    json_text = json_text.replace('\\\"', '"').replace('\\n', '\n')
                try:
                    return json.loads(json_text)
                except json.JSONDecodeError as e:
                    logger.warning(f"🔍 JSON decode error: {str(e)}")
                    # Try cleaning up common formatting issues
                    json_text = json_text.replace('\n', ' ').replace('\t', ' ')
                    # Remove extra spaces
                    json_text = re.sub(r'\s+', ' ', json_text)
                    return json.loads(json_text)

        # Log the content for debugging if no JSON found
        logger.warning(f"No valid JSON structure found in content: {content[:200]}...")
        return None

    except Exception as e:
        logger.error(f"JSON parsing error: {str(e)}")
        logger.debug(f"Content that failed to parse: {content[:500]}")
        return None

def download_s3_file(bucket: str, key: str) -> str:
    """Download and read content from S3 file (supports txt, docx, pdf)"""
    try:
        import tempfile
        
        # Download file to temporary location
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            s3_client.download_fileobj(bucket, key, temp_file)
            temp_path = temp_file.name
        
        try:
            # Determine file type from S3 key extension
            file_extension = Path(key).suffix.lower()
            
            if file_extension == '.txt':
                with open(temp_path, 'r', encoding='utf-8') as file:
                    return file.read()
            
            elif file_extension == '.docx':
                doc = Document(temp_path)
                return '\n'.join([paragraph.text for paragraph in doc.paragraphs])
            
            elif file_extension == '.pdf':
                with open(temp_path, 'rb') as file:
                    pdf_reader = PyPDF2.PdfReader(file)
                    text = ''
                    for page in pdf_reader.pages:
                        text += page.extract_text() + '\n'
                    return text
            
            else:
                # Default to text for unknown extensions
                with open(temp_path, 'r', encoding='utf-8') as file:
                    return file.read()
                    
        finally:
            # Clean up temporary file
            os.unlink(temp_path)
            
    except Exception as e:
        logger.error(f"Error downloading/reading S3 file {bucket}/{key}: {str(e)}")
        raise

def extract_name_from_key(s3_key: str) -> str:
    """Extract candidate name from S3 key"""
    try:
        filename = Path(s3_key).stem
        name = filename.replace('_', ' ').replace('-', ' ')
        return ' '.join(word.capitalize() for word in name.split())
    except:
        return "Unknown Candidate"

if __name__ == "__main__":
    # async def test():
    #     # First call with document payload to create session
    #     document_payload = {
    #         "bucket": "amzn-s3-resume-analyzer-bucket-agentcore-206409480438",
    #         "resume_key": "resumes/20251125_124020_john_smith_resume.txt",
    #         "job_description_key": "jobs/20251125_132758_job_description.txt"
    #     }
        
    #     print("=== First call: Processing documents ===")
    #     response1 = ""
    #     async for chunk in invoke(document_payload):
    #         response1 += str(chunk)
    #     print(f"Document processing result: {response1[:200]}...")
        
    #     # Second call with query only - should use existing session
    #     query_payload = {
    #         "query": "What is the candidate's overall score?"
    #     }
        
    #     print("\n=== Second call: Follow-up query ===")
    #     response2 = ""
    #     async for chunk in invoke(query_payload):
    #         response2 += str(chunk)
    #     print(f"Query result: {response2[:200]}...")
    
    # asyncio.run(test())
    app.run()


