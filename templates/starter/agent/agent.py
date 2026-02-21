"""Starter agent for three-stars.

This agent receives messages from the frontend via the /api/invoke endpoint
and responds using Amazon Bedrock.

Customize this file with your agent logic.
"""

import json
import os

from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
def handler(request):
    """Handle incoming requests from the frontend.

    Args:
        request: Request payload dict with 'message' field.

    Returns:
        Response dict with 'message' field.
    """
    user_message = request.get("message") or request.get("prompt", "")

    if not user_message:
        return {"message": "Please send a message."}

    # Call Bedrock for a response
    import boto3

    model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")

    try:
        bedrock = boto3.client("bedrock-runtime")
        response = bedrock.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "messages": [
                    {"role": "user", "content": user_message}
                ],
            }),
        )
        result = json.loads(response["body"].read())
        assistant_message = result["content"][0]["text"]
    except Exception as e:
        assistant_message = f"I'm having trouble connecting to the model. Error: {e}"

    return {"message": assistant_message}


if __name__ == "__main__":
    app.run()
