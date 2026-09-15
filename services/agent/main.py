"""Amazon Bedrock AgentCore Runtime entrypoint for QuietPilot."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from quietpilot_agent.agentcore_runtime import run_agentcore_invocation
from quietpilot_agent.google_connector import GoogleAuthorizationRequired

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: object, context: object) -> dict[str, object]:
    try:
        return run_agentcore_invocation(payload, context)
    except GoogleAuthorizationRequired:
        return {
            "status": "AUTHORIZATION_REQUIRED",
            "error_code": "GOOGLE_AUTH_REQUIRED",
        }


if __name__ == "__main__":
    app.run()
