"""Starter agent for three-stars.

This agent receives messages from the frontend via the /api/invoke endpoint
and responds using a Strands Agent powered by Amazon Bedrock.

The handler is an async generator — BedrockAgentCoreApp wraps it in an SSE
StreamingResponse so tokens arrive at the client as they are produced.

Customize this file with your agent logic — add tools, change the model,
or adjust the system prompt.
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

app = BedrockAgentCoreApp()

model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
model = BedrockModel(model_id=model_id, region_name=region)
agent = Agent(model=model, system_prompt="You are a helpful AI assistant. Be concise and friendly.")


@app.entrypoint
async def handler(payload):
    """Handle incoming requests from the frontend.

    Yields streaming events from the Strands Agent. BedrockAgentCoreApp
    detects the async generator and wraps it in an SSE StreamingResponse.

    Args:
        payload: Request payload dict with 'prompt' or 'message' field.
    """
    user_message = payload.get("prompt") or payload.get("message", "")

    if not user_message:
        yield {"message": "Please send a message."}
        return

    stream = agent.stream_async(user_message)
    async for event in stream:
        if not isinstance(event, dict):
            continue
        # Skip the final result event (contains non-serializable AgentResult)
        if "result" in event:
            continue
        # Extract text delta from streaming chunks
        text = (event.get("data")
                or (event.get("delta") or {}).get("text")
                or "")
        if text:
            yield {"data": text}


if __name__ == "__main__":
    app.run()
