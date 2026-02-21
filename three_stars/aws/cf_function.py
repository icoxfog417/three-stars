"""CloudFront Functions management for API routing."""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

# JavaScript code for the CloudFront Function that routes API requests.
# AGENTCORE_ENDPOINT placeholder is replaced at creation time.
ROUTER_FUNCTION_TEMPLATE = """\
function handler(event) {{
    var request = event.request;
    var uri = request.uri;

    if (uri.startsWith('{api_prefix}/') || uri === '{api_prefix}') {{
        // Route API requests to AgentCore endpoint
        var newUri = uri.substring({prefix_len});
        if (newUri === '' || newUri === '/') {{
            newUri = '/';
        }}

        request.origin = {{
            custom: {{
                domainName: '{agentcore_host}',
                port: 443,
                protocol: 'https',
                path: '{agentcore_path}',
                sslProtocols: ['TLSv1.2'],
                readTimeout: 30,
                keepaliveTimeout: 5
            }}
        }};
        request.uri = newUri;
        request.headers['host'] = {{ value: '{agentcore_host}' }};
    }}

    return request;
}}
"""


def _build_function_code(
    agentcore_endpoint: str,
    api_prefix: str = "/api",
) -> str:
    """Build the CloudFront Function JavaScript code.

    Args:
        agentcore_endpoint: Full URL of the AgentCore endpoint.
        api_prefix: URL prefix that triggers API routing.

    Returns:
        JavaScript function code as a string.
    """
    # Parse the endpoint URL
    from urllib.parse import urlparse

    parsed = urlparse(agentcore_endpoint)
    host = parsed.hostname or agentcore_endpoint
    path = parsed.path.rstrip("/") if parsed.path else ""

    return ROUTER_FUNCTION_TEMPLATE.format(
        api_prefix=api_prefix,
        prefix_len=len(api_prefix),
        agentcore_host=host,
        agentcore_path=path,
    )


def create_function(
    session: boto3.Session,
    name: str,
    agentcore_endpoint: str,
    api_prefix: str = "/api",
) -> str:
    """Create and publish a CloudFront Function for API routing.

    Args:
        session: boto3 session.
        name: Function name.
        agentcore_endpoint: AgentCore endpoint URL.
        api_prefix: API path prefix.

    Returns:
        Function ARN.
    """
    cf = session.client("cloudfront")
    code = _build_function_code(agentcore_endpoint, api_prefix)

    resp = cf.create_function(
        Name=name,
        FunctionConfig={
            "Comment": f"API router for three-stars ({name})",
            "Runtime": "cloudfront-js-2.0",
        },
        FunctionCode=code.encode("utf-8"),
    )

    # Publish the function to make it available for association
    etag = resp["ETag"]
    publish_resp = cf.publish_function(Name=name, IfMatch=etag)

    return publish_resp["FunctionSummary"]["FunctionMetadata"]["FunctionARN"]


def update_function(
    session: boto3.Session,
    name: str,
    agentcore_endpoint: str,
    api_prefix: str = "/api",
) -> str:
    """Update an existing CloudFront Function with new routing config.

    Returns the updated Function ARN.
    """
    cf = session.client("cloudfront")
    code = _build_function_code(agentcore_endpoint, api_prefix)

    # Get current ETag
    desc_resp = cf.describe_function(Name=name)
    etag = desc_resp["ETag"]

    resp = cf.update_function(
        Name=name,
        FunctionConfig={
            "Comment": f"API router for three-stars ({name})",
            "Runtime": "cloudfront-js-2.0",
        },
        FunctionCode=code.encode("utf-8"),
        IfMatch=etag,
    )

    # Publish update
    etag = resp["ETag"]
    publish_resp = cf.publish_function(Name=name, IfMatch=etag)
    return publish_resp["FunctionSummary"]["FunctionMetadata"]["FunctionARN"]


def delete_function(session: boto3.Session, name: str) -> None:
    """Delete a CloudFront Function."""
    cf = session.client("cloudfront")
    try:
        desc_resp = cf.describe_function(Name=name)
        etag = desc_resp["ETag"]
        cf.delete_function(Name=name, IfMatch=etag)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchFunctionExists":
            return
        raise
