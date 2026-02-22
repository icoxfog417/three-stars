"""Starter agent for three-stars.

This agent receives messages from the frontend via the /api/invoke endpoint
and responds using a Strands Agent powered by Amazon Bedrock.

Customize this file with your agent logic — add tools, change the model,
or adjust the system prompt.
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

app = BedrockAgentCoreApp()

model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

model = BedrockModel(model_id=model_id, region_name=region)
agent = Agent(
    model=model,
    system_prompt="You are a helpful AI assistant. Be concise and friendly.",
)


@app.entrypoint
def handler(payload):
    """Handle incoming requests from the frontend.

    Args:
        payload: Request payload dict with 'prompt' or 'message' field.

    Returns:
        Response dict with 'message' field.
    """
    user_message = payload.get("prompt") or payload.get("message", "")

    if not user_message:
        return {"message": "Please send a message."}

    result = agent(user_message)
    return {"message": result.message}


if __name__ == "__main__":
    app.run()
