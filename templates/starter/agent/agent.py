"""Starter agent for three-stars.

This agent receives messages from the frontend via the /api/invoke endpoint
and responds using Amazon Bedrock.

Customize this file with your agent logic.
"""

import json
import os

import boto3


def handler(event, context=None):
    """Handle incoming requests from the frontend.

    Args:
        event: Request payload with 'message' field.
        context: Runtime context (optional).

    Returns:
        Response dict with 'message' field.
    """
    # Parse the incoming message
    body = event if isinstance(event, dict) else json.loads(event)
    user_message = body.get("message", "")

    if not user_message:
        return {"message": "Please send a message."}

    # Call Bedrock for a response
    model_id = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-20250514")

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
