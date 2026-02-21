# Proposal: Fix AgentCore Runtime Deployment (Critical)

**Date**: 2026-02-21
**Author**: Claude Agent
**Status**: Proposed
**Reference**: bedrock-agentcore-starter-toolkit (https://github.com/aws/bedrock-agentcore-starter-toolkit)

## Background

During E2E evaluation, AgentCore runtime deployment consistently failed with initialization timeout. All 5 invocation attempts returned:

```
Agent invocation error: An error occurred (DependencyFailedException) when calling
the InvokeAgentRuntime operation: Initialization of runtime <id> timed out
```

Investigation of the official `bedrock-agentcore-starter-toolkit` reveals **three-stars has critical bugs** in its deployment pipeline.

## Root Cause Analysis

### P0 — Dependencies NOT bundled in deployment zip (BLOCKS EVERYTHING)

**This is the root cause of initialization failure.**

Three-stars `_package_agent()` (`agentcore.py:154-168`) simply zips the user's source directory:

```python
def _package_agent(agent_dir):
    for file_path in sorted(agent_path.rglob("*")):
        zf.write(file_path, arcname)
```

This produces a zip containing only:
- `agent.py`
- `requirements.txt`

The `requirements.txt` file is **never processed** — pip dependencies are never installed and bundled. When AgentCore runtime starts, it tries to execute `agent.py` which does `from bedrock_agentcore import BedrockAgentCoreApp`, but the `bedrock_agentcore` package doesn't exist in the deployment zip. Import fails → initialization times out.

**How the starter toolkit does it** (`package.py:CodeZipPackager`):

1. **Build `dependencies.zip`**: Uses `uv pip install --target <dir> -r requirements.txt` to install all pip packages into a flat directory, cross-compiled for Linux ARM64 (`aarch64-manylinux2014`), then zips them
2. **Build `code.zip`**: Packages user source code
3. **Merge**: Combines dependencies + code into `deployment.zip` (dependencies first, code overwrites on conflict)
4. **Upload**: Uploads merged zip to S3

The resulting zip contains both the source code AND all installed Python packages — a self-contained deployable artifact.

### P1 — Lambda bridge invocation missing `runtimeSessionId`

Three-stars Lambda bridge (`api_bridge.py:23-53`) calls:

```python
resp = client.invoke_agent_runtime(
    agentRuntimeArn=runtime_arn,
    payload=body.encode("utf-8"),
    contentType="application/json",
)
```

But the starter toolkit's invocation (`runtime.py:612-671`) shows required parameters:

```python
req = {
    "agentRuntimeArn": agent_arn,
    "qualifier": endpoint_name,       # MISSING in three-stars
    "runtimeSessionId": session_id,   # MISSING in three-stars (REQUIRED)
    "payload": payload,
    "contentType": "application/json",
}
```

Missing `runtimeSessionId` will cause invocation to fail even after the deployment zip is fixed.

### P1 — Lambda timeout too short (30s)

Three-stars sets Lambda timeout to 30 seconds (`api_bridge.py:244`). The starter toolkit uses `read_timeout=900` (15 minutes). Agent responses from LLMs typically take 5-30+ seconds. 30s is too short for any real conversation.

### P1 — Response handling assumes synchronous body

Three-stars reads the response as:
```python
response_body = resp["response"].read().decode("utf-8")
```

But AgentCore returns an EventStream. The starter toolkit handles this correctly with:
```python
if "text/event-stream" in response.get("contentType", ""):
    return _handle_streaming_response(response["response"])
```

### P2 — Cross-compilation not done for deployment dependencies

The starter toolkit always cross-compiles for `aarch64-manylinux2014` because AgentCore Runtime uses ARM64 Linux. Three-stars doesn't do dependency installation at all, but when fixed, it must also cross-compile.

## Proposal: Required Changes

### Fix 1: Bundle dependencies in deployment zip (P0)

Replace `_package_agent()` with a proper packaging pipeline:

```python
def _package_agent(agent_dir: Path) -> bytes:
    """Package agent source + dependencies into a deployment zip."""
    # 1. Find requirements.txt in agent_dir
    requirements = agent_dir / "requirements.txt"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        deps_dir = tmp_path / "deps"
        deps_dir.mkdir()

        # 2. Install dependencies via uv (or pip) for ARM64 Linux
        if requirements.exists():
            subprocess.run([
                "uv", "pip", "install",
                "--target", str(deps_dir),
                "--python-version", "3.13",
                "--python-platform", "aarch64-manylinux2014",
                "--only-binary", ":all:",
                "--upgrade",
                "-r", str(requirements),
            ], check=True)

        # 3. Create merged zip: dependencies + source code
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # Layer 1: Dependencies
            for file_path in deps_dir.rglob("*"):
                if file_path.is_file() and "__pycache__" not in file_path.parts:
                    zf.write(file_path, file_path.relative_to(deps_dir))

            # Layer 2: Source code (overwrites on conflict)
            for file_path in agent_dir.rglob("*"):
                if file_path.is_file() and "__pycache__" not in file_path.parts:
                    zf.write(file_path, file_path.relative_to(agent_dir))

    return buffer.getvalue()
```

**Dependencies**: Requires `uv` to be installed. Fallback to `pip install --target` if uv is not available.

### Fix 2: Add `runtimeSessionId` to Lambda invocation (P1)

```python
import uuid

def handler(event, context):
    client = boto3.client("bedrock-agentcore")
    body = event.get("body", "{}")

    # Parse body to extract session_id, or generate one
    try:
        parsed = json.loads(body)
        session_id = parsed.get("session_id", str(uuid.uuid4()))
    except:
        session_id = str(uuid.uuid4())

    resp = client.invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
        qualifier="DEFAULT",
        runtimeSessionId=session_id,
        payload=body,
        contentType="application/json",
    )

    # Handle EventStream response
    content_type = resp.get("contentType", "")
    if "text/event-stream" in content_type:
        # Collect streaming events
        chunks = []
        for event_data in resp["response"]:
            if isinstance(event_data, bytes):
                chunks.append(event_data.decode("utf-8"))
        response_body = "".join(chunks)
    else:
        response_body = resp["response"].read().decode("utf-8")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": response_body,
    }
```

### Fix 3: Increase Lambda timeout (P1)

Change from 30s to 120s minimum (300s recommended):

```python
# api_bridge.py line ~244
"Timeout": 300,  # was 30
```

### Fix 4: Default to PYTHON_3_11 (P2)

The starter toolkit defaults to `PYTHON_3_11`. It's the most tested runtime:

```python
# agentcore.py _create_agent_runtime
runtime: str = "PYTHON_3_11",  # was PYTHON_3_13
```

## Impact

- **requirements.md**: No changes needed
- **design.md**: Update deployment architecture to document dependency bundling
- **tasks.md**: Add implementation tasks for all 4 fixes

## Alternatives Considered

1. **Container deployment (ECR + Docker)**: The starter toolkit's primary path uses Docker/CodeBuild. More reliable but much more complex. The `direct_code_deploy` (code zip) path is simpler and appropriate for three-stars's use case, but only if dependencies are properly bundled.

2. **Pre-built layer approach**: Could create a shared Lambda Layer with common dependencies. Rejected because AgentCore Runtime doesn't use Lambda Layers — it's a different compute platform.

## Implementation Plan

1. Install `uv` as a build dependency (or check for availability)
2. Rewrite `_package_agent()` to install + bundle dependencies
3. Fix Lambda bridge invocation parameters
4. Increase Lambda timeout
5. Add integration test verifying the zip contains expected packages
6. Re-run E2E deployment test
