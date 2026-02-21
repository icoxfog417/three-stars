"""API bridge resource module — Lambda function + IAM role + function URL."""

from __future__ import annotations

import io
import json
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

from three_stars.naming import ResourceNames
from three_stars.resources import ResourceStatus
from three_stars.state import ApiBridgeState

_BRIDGE_FUNCTION_CODE = '''\
import json
import os
import uuid
import boto3


def handler(event, context):
    """Bridge HTTP requests to AgentCore invoke_agent_runtime."""
    client = boto3.client("bedrock-agentcore")

    body = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode()

    # Extract or generate a session id for AgentCore
    try:
        parsed = json.loads(body) if isinstance(body, str) else json.loads(body.decode("utf-8"))
        session_id = parsed.get("session_id", str(uuid.uuid4()))
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        session_id = str(uuid.uuid4())

    runtime_arn = os.environ["AGENT_RUNTIME_ARN"]
    endpoint_name = os.environ.get("AGENT_ENDPOINT_NAME", "DEFAULT")

    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            qualifier=endpoint_name,
            runtimeSessionId=session_id,
            payload=body.encode("utf-8") if isinstance(body, str) else body,
            contentType="application/json",
        )

        # Handle EventStream or plain responses
        content_type = resp.get("contentType", "")
        if "text/event-stream" in content_type:
            chunks = []
            for event_data in resp["response"]:
                if isinstance(event_data, dict):
                    chunk = event_data.get("chunk", {})
                    if "bytes" in chunk:
                        chunks.append(chunk["bytes"].decode("utf-8"))
                elif isinstance(event_data, bytes):
                    chunks.append(event_data.decode("utf-8"))
            response_body = "".join(chunks)
        else:
            response_body = resp["response"].read().decode("utf-8")

        status_code = resp.get("statusCode", 200)
    except Exception as e:
        response_body = json.dumps({"message": f"Agent invocation error: {e}"})
        status_code = 500

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": response_body,
    }
'''


def deploy(
    session: boto3.Session,
    config,
    names: ResourceNames,
    *,
    agent_runtime_arn: str,
    endpoint_name: str = "DEFAULT",
    tags: list[dict[str, str]] | None = None,
    tags_dict: dict[str, str] | None = None,
) -> ApiBridgeState:
    """Create or update Lambda bridge function + IAM role + function URL.

    Args:
        agent_runtime_arn: From agentcore.deploy() output — passed by orchestrator.
        endpoint_name: AgentCore endpoint name for qualifier.
        tags: AWS tag list format for IAM roles.
        tags_dict: Dict format tags for Lambda functions.
    """
    account_id = session.client("sts").get_caller_identity()["Account"]

    role_arn = _create_lambda_role(session, names.lambda_role, account_id, config.region, tags=tags)

    lambda_info = _create_lambda_function(
        session,
        function_name=names.lambda_function,
        role_arn=role_arn,
        agent_runtime_arn=agent_runtime_arn,
        endpoint_name=endpoint_name,
        region=config.region,
        tags=tags_dict,
    )

    return ApiBridgeState(
        role_name=names.lambda_role,
        role_arn=role_arn,
        function_name=lambda_info["function_name"],
        function_arn=lambda_info["function_arn"],
        function_url=lambda_info["function_url"],
    )


def destroy(session: boto3.Session, state: ApiBridgeState) -> None:
    """Delete Lambda bridge function and IAM role."""
    _delete_lambda_function(session, state.function_name)
    _delete_lambda_role(session, state.role_name)


def get_status(session: boto3.Session, state: ApiBridgeState) -> list[ResourceStatus]:
    """Return Lambda bridge status."""
    rows: list[ResourceStatus] = []

    name = "Lambda Bridge"
    fn = state.function_name
    try:
        lam = session.client("lambda")
        resp = lam.get_function(FunctionName=fn)
        fn_state = resp["Configuration"]["State"]
        if fn_state == "Active":
            rows.append(ResourceStatus(name, fn, "[green]Active[/green]"))
        else:
            rows.append(ResourceStatus(name, fn, f"[yellow]{fn_state}[/yellow]"))
    except Exception:
        rows.append(ResourceStatus(name, fn, "[red]Not Found[/red]"))

    rows.append(ResourceStatus("Lambda IAM Role", state.role_name, "[green]Active[/green]"))

    return rows


def grant_cloudfront_access(
    session: boto3.Session,
    function_name: str,
    distribution_arn: str,
) -> None:
    """Grant CloudFront OAC permission to invoke the Lambda function URL."""
    lam = session.client("lambda")
    try:
        lam.add_permission(
            FunctionName=function_name,
            StatementId="AllowCloudFrontOAC",
            Action="lambda:InvokeFunctionUrl",
            Principal="cloudfront.amazonaws.com",
            SourceArn=distribution_arn,
            FunctionUrlAuthType="AWS_IAM",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise


def _build_function_zip() -> bytes:
    """Package the bridge function code into a zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.py", _BRIDGE_FUNCTION_CODE)
    return buffer.getvalue()


def _create_lambda_role(
    session: boto3.Session,
    role_name: str,
    account_id: str,
    region: str,
    tags: list[dict[str, str]] | None = None,
) -> str:
    """Create an IAM role for the Lambda bridge function. Returns the role ARN."""
    iam = session.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    created = False
    try:
        create_kwargs: dict = {
            "RoleName": role_name,
            "AssumeRolePolicyDocument": json.dumps(trust_policy),
            "Description": "Execution role for three-stars Lambda bridge",
        }
        if tags:
            create_kwargs["Tags"] = tags
        resp = iam.create_role(**create_kwargs)
        role_arn = resp["Role"]["Arn"]
        created = True
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam.get_role(RoleName=role_name)
            role_arn = resp["Role"]["Arn"]
            if tags:
                iam.tag_role(RoleName=role_name, Tags=tags)
        else:
            raise

    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": f"arn:aws:logs:{region}:{account_id}:*",
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": "*",
            },
        ],
    }

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="three-stars-lambda-bridge-policy",
        PolicyDocument=json.dumps(inline_policy),
    )

    if created:
        time.sleep(10)
    return role_arn


def _create_lambda_function(
    session: boto3.Session,
    function_name: str,
    role_arn: str,
    agent_runtime_arn: str,
    endpoint_name: str = "DEFAULT",
    region: str = "us-east-1",
    tags: dict[str, str] | None = None,
) -> dict:
    """Create the Lambda bridge function with a function URL."""
    lam = session.client("lambda")
    zip_bytes = _build_function_zip()

    env_vars = {
        "AGENT_RUNTIME_ARN": agent_runtime_arn,
        "AGENT_ENDPOINT_NAME": endpoint_name,
    }

    try:
        create_kwargs: dict = {
            "FunctionName": function_name,
            "Runtime": "python3.11",
            "Role": role_arn,
            "Handler": "index.handler",
            "Code": {"ZipFile": zip_bytes},
            "Timeout": 300,
            "MemorySize": 256,
            "Environment": {"Variables": env_vars},
        }
        if tags:
            create_kwargs["Tags"] = tags
        resp = lam.create_function(**create_kwargs)
        function_arn = resp["FunctionArn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            _wait_for_lambda_active(lam, function_name)
            lam.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
            _wait_for_lambda_active(lam, function_name)
            lam.update_function_configuration(
                FunctionName=function_name,
                Environment={"Variables": env_vars},
            )
            resp = lam.get_function(FunctionName=function_name)
            function_arn = resp["Configuration"]["FunctionArn"]
        else:
            raise

    _wait_for_lambda_active(lam, function_name)

    lam.put_function_concurrency(
        FunctionName=function_name,
        ReservedConcurrentExecutions=10,
    )

    function_url = _ensure_function_url(lam, function_name)

    return {
        "function_name": function_name,
        "function_arn": function_arn,
        "function_url": function_url,
    }


def _wait_for_lambda_active(
    lam,
    function_name: str,
    max_wait_seconds: int = 60,
    poll_interval: int = 2,
) -> None:
    """Wait for Lambda function to reach Active state."""
    start = time.time()
    while time.time() - start < max_wait_seconds:
        resp = lam.get_function(FunctionName=function_name)
        config = resp["Configuration"]
        state = config["State"]
        last_update = config.get("LastUpdateStatus", "Successful")
        if state == "Active" and last_update in ("Successful", None):
            return
        if state == "Failed":
            raise RuntimeError(
                f"Lambda function {function_name} failed: {config.get('StateReason', 'unknown')}"
            )
        if last_update == "Failed":
            raise RuntimeError(
                f"Lambda function {function_name} update failed: "
                f"{config.get('LastUpdateStatusReason', 'unknown')}"
            )
        time.sleep(poll_interval)

    raise TimeoutError(f"Lambda {function_name} did not become Active within {max_wait_seconds}s")


def _ensure_function_url(lam, function_name: str) -> str:
    """Create or get the function URL. Uses AuthType=AWS_IAM."""
    try:
        resp = lam.get_function_url_config(FunctionName=function_name)
        return resp["FunctionUrl"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    resp = lam.create_function_url_config(
        FunctionName=function_name,
        AuthType="AWS_IAM",
    )
    return resp["FunctionUrl"]


def _delete_lambda_function(session: boto3.Session, function_name: str) -> None:
    """Delete a Lambda function."""
    lam = session.client("lambda")
    try:
        lam.delete_function(FunctionName=function_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return
        raise


def _delete_lambda_role(session: boto3.Session, role_name: str) -> None:
    """Delete the Lambda bridge IAM role and its inline policies."""
    iam = session.client("iam")
    try:
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            return
        raise
