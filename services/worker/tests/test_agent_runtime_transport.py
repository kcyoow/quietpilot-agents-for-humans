from __future__ import annotations

import io
import json
import os
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ReadTimeoutError
from quietpilot_worker.google_jobs import BotoAgentRuntime


@pytest.fixture
def runtime_client(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", os.devnull)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "7")
    monkeypatch.setenv(
        "AGENTCORE_RUNTIME_ARN",
        "arn:aws:bedrock-agentcore:ap-northeast-2:000000000000:runtime/synthetic_agent-abcdefghij",
    )
    session = boto3.Session(
        aws_access_key_id="synthetic-access-key",
        aws_secret_access_key="synthetic-secret-key",
        region_name="ap-northeast-2",
    )
    clients = []

    def create_client(service_name, **kwargs):
        client = session.client(
            service_name, endpoint_url="https://agent-runtime.invalid", **kwargs
        )
        clients.append(client)
        return client

    monkeypatch.setattr(boto3, "client", create_client)
    # A regression can retry without making this test sleep or use the network.
    monkeypatch.setattr("botocore.endpoint.time.sleep", lambda seconds: None)
    runtime = BotoAgentRuntime.from_environment()
    assert len(clients) == 1
    yield runtime, clients[0]
    clients[0].close()


def test_read_timeout_makes_exactly_one_real_botocore_transport_attempt(
    runtime_client, monkeypatch
):
    runtime, client = runtime_client
    attempts = []

    def timeout(request):
        attempts.append(request)
        raise ReadTimeoutError(endpoint_url=request.url)

    monkeypatch.setattr(client._endpoint.http_session, "send", timeout)
    with pytest.raises(ReadTimeoutError):
        runtime.invoke("synthetic-owner", "GOOGLE_INTEREST_TAGS")

    assert len(attempts) == 1
    assert client.meta.config.connect_timeout == 5
    assert client.meta.config.read_timeout == 90


def test_explicit_retry_still_works_and_preserves_the_runtime_wire_contract(
    runtime_client, monkeypatch
):
    runtime, client = runtime_client
    requests = []
    fail = True
    result = {"status": "INTEREST_TAGS", "tags": [], "title_count": 0}
    body = json.dumps(result).encode()

    def send(request):
        requests.append(request)
        if fail:
            raise ReadTimeoutError(endpoint_url=request.url)
        return AWSResponse(
            request.url,
            200,
            {"content-type": "application/json", "content-length": str(len(body))},
            io.BytesIO(body),
        )

    monkeypatch.setattr(client._endpoint.http_session, "send", send)
    with pytest.raises(ReadTimeoutError):
        runtime.invoke("synthetic-owner", "GOOGLE_INTEREST_TAGS")
    assert len(requests) == 1

    fail = False
    assert runtime.invoke("synthetic-owner", "GOOGLE_INTEREST_TAGS") == result
    assert len(requests) == 2
    request = requests[-1]
    assert request.method == "POST"
    assert parse_qs(urlsplit(request.url).query) == {"qualifier": ["DEFAULT"]}
    assert json.loads(request.body) == {
        "operation": "GOOGLE_INTEREST_TAGS",
        "user_id": "synthetic-owner",
    }
    headers = {
        key.lower(): value.decode() if isinstance(value, bytes) else value
        for key, value in request.headers.items()
    }
    assert headers["x-amzn-bedrock-agentcore-runtime-user-id"] == "synthetic-owner"
    assert headers["x-amzn-bedrock-agentcore-runtime-session-id"].startswith(
        "quietpilot-"
    )
