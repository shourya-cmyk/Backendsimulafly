from types import SimpleNamespace

import httpx
import pytest


@pytest.mark.asyncio
async def test_sandbox_client_uses_documented_auth_pan_and_gstin_contracts(monkeypatch):
    from app.services import sandbox_client as module

    settings = SimpleNamespace(
        SANDBOX_API_BASE_URL="https://test-api.sandbox.co.in",
        SANDBOX_API_KEY="key_test_example",
        SANDBOX_API_SECRET="secret_test_example",
        SANDBOX_API_VERSION="1.0.0",
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)

    responses = iter(
        [
            httpx.Response(200, json={"code": 200, "data": {"access_token": "jwt-token"}}),
            httpx.Response(
                200,
                json={
                    "code": 200,
                    "transaction_id": "pan-tx",
                    "data": {
                        "category": "company",
                        "status": "valid",
                        "remarks": None,
                        "name_as_per_pan_match": True,
                        "date_of_birth_match": True,
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "code": 200,
                    "transaction_id": "gst-tx",
                    "data": {
                        "status_cd": "1",
                        "data": {
                            "legalName": "ACME PRIVATE LIMITED",
                            "bussNature": "Retail Business",
                            "stateName": "Maharashtra",
                            "validGstin": True,
                            "stateCode": "27",
                            "pan": "ABCDE1234F",
                            "gstin": "27ABCDE1234F1Z5",
                            "regStartDate": "01/07/2017",
                            "status": "Active",
                        },
                    },
                },
            ),
        ]
    )
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return next(responses)

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)

    client = module.SandboxClient()
    pan_result = await client.verify_pan(
        pan="ABCDE1234F",
        name_as_per_pan="Acme Private Limited",
        date_of_birth="20/08/2015",
    )
    gstin_result = await client.verify_gstin(gstin="27ABCDE1234F1Z5")

    auth_url, auth_call = calls[0]
    assert auth_url == "https://test-api.sandbox.co.in/authenticate"
    assert auth_call["headers"] == {
        "x-api-key": "key_test_example",
        "x-api-secret": "secret_test_example",
        "x-api-version": "1.0",
    }

    pan_url, pan_call = calls[1]
    assert pan_url.endswith("/kyc/pan/verify")
    assert pan_call["headers"]["authorization"] == "jwt-token"
    assert not pan_call["headers"]["authorization"].startswith("Bearer ")
    assert pan_call["headers"]["x-api-version"] == "1.0.0"
    assert pan_call["json"]["@entity"] == "in.co.sandbox.kyc.pan_verification.request"
    assert pan_call["json"]["consent"] == "Y"
    assert pan_result.name_match is True

    gstin_url, gstin_call = calls[2]
    assert gstin_url.endswith("/gst/compliance/public/gstin/verify")
    assert gstin_call["json"] == {"gstin": "27ABCDE1234F1Z5"}
    assert gstin_result.legal_name == "ACME PRIVATE LIMITED"
    assert gstin_result.valid_gstin is True
