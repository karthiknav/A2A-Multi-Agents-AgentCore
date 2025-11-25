#!/bin/bash

# Resume Analyzer Agents - Strands AgentCore Implementation Deployment Script
set -e

ENVIRONMENT="agentcore"
STACK_NAME="resume-analyzer-agents-strands-${ENVIRONMENT}"
REGION=${AWS_DEFAULT_REGION:-us-east-1}

echo "🚀 Deploying Resume Analyzer Agents - Strands AgentCore Implementation"
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo "Stack: $STACK_NAME"

# Check prerequisites
echo "🔍 Checking prerequisites..."

# Check AWS CLI
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI is required but not installed"
    exit 1
fi

# Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "📦 Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Setup uv environment and install dependencies
echo "📦 Setting up uv environment and installing dependencies..."
uv venv --clear
source .venv/bin/activate || source .venv/Scripts/activate
uv pip install -r requirements.txt
export UV_PROJECT_ENVIRONMENT=.venv

# Step 1: Deploy infrastructure
echo "🏗️ Step 1: Deploying infrastructure..."
aws cloudformation deploy \
    --template-file template-infrastructure.yaml \
    --stack-name $STACK_NAME \
    --parameter-overrides Environment=$ENVIRONMENT \
    --capabilities CAPABILITY_NAMED_IAM \
    --region $REGION

# Get infrastructure outputs
echo "📋 Getting infrastructure outputs..."
DOCUMENTS_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --region $REGION \
    --query 'Stacks[0].Outputs[?OutputKey==`DocumentsBucket`].OutputValue' \
    --output text)


echo "✅ Infrastructure deployed:"
echo "  Documents Bucket: $DOCUMENTS_BUCKET"


# Step 2: Configure AgentCore
echo "🤖 Step 2: Configuring AgentCore agent..."

# Set environment variables for the agent
export DOCUMENTS_BUCKET=$DOCUMENTS_BUCKET
export EXECUTION_ROLE="arn:aws:iam::206409480438:role/AmazonBedrockAgentCoreExecutionRolev1"

# Configure the agent using starter toolkit with execution role
echo "Configuring AgentCore with execution role: $EXECUTION_ROLE"
agentcore configure \
    --entrypoint resume_analyzer_agent.py \
    --name "resume_analyzer_agent" \
    --region us-east-1 \
    --execution-role $EXECUTION_ROLE \
    --ecr "206409480438.dkr.ecr.us-east-1.amazonaws.com/resume_analyzer_agent"

echo "✅ AgentCore configuration completed"

# Step 3: Deploy agent to AgentCore Runtime
echo "🚀 Step 3: Deploying agent to AgentCore Runtime..."
agentcore launch

# Get agent ARN from configuration
if [ -f ".bedrock_agentcore.yaml" ]; then
    AGENT_ARN=$(grep -A 10 "bedrock_agentcore:" bedrock_agentcore.yaml | grep "arn:" | awk '{print $2}' | tr -d '"')
    echo "✅ Agent deployed with ARN: $AGENT_ARN"
else
    echo "❌ Could not find bedrock_agentcore.yaml configuration file"
    exit 1
fi


# Step 5: Test the deployment
echo "🧪 Step 5: Testing the deployment..."

# Test the AgentCore agent directly
echo "Testing AgentCore agent..."
#agentcore invoke '{"bucket": "'$DOCUMENTS_BUCKET'", "resume_key": "test/sample.txt", "candidate_id": "test-123"}'

echo ""
echo "🎉 Deployment completed successfully!"
echo ""
echo "📋 Summary:"
echo "  Environment: $ENVIRONMENT"
echo "  Region: $REGION"
echo "  Documents Bucket: $DOCUMENTS_BUCKET"
echo "  Agent ARN: $AGENT_ARN"
echo ""
echo "📝 Next steps:"
echo "  1. Upload job descriptions and to s3://$DOCUMENTS_BUCKET/jobs/"
echo "  2. Upload resumes to s3://$DOCUMENTS_BUCKET/resumes/"
