"""Starter agent for three-stars.

This agent receives messages from the frontend via the /api/invoke endpoint
and responds using Amazon Bedrock's Converse API.

Customize this file with your agent logic — add tools, change the model,
or adjust the system prompt.
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

_client = None

SYSTEM_PROMPT = "You are a helpful AI assistant. Be concise and friendly."


def _get_client():
    """Lazy-init the Bedrock Runtime client on first request."""
    global _client
    if _client is None:
        import boto3

        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        _client = boto3.client("bedrock-runtime", region_name=region)
    return _client


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

    model_id = os.environ.get(
        "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"
    )
    client = _get_client()
    resp = client.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_message}]}],
    )
    reply = resp["output"]["message"]["content"][0]["text"]
    return {"message": reply}


if __name__ == "__main__":
    app.run()
