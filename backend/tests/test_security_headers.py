from fastapi.testclient import TestClient

from app.main import app


def test_api_responses_are_not_cached_or_embeddable():
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    policy = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy
    assert "frame-src 'none'" in policy
    assert "connect-src 'self'" in policy


def test_https_responses_enable_hsts():
    response = TestClient(app, base_url="https://erp.example.test").get("/api/health")

    assert response.headers["strict-transport-security"].startswith("max-age=31536000")


def test_a_rejected_body_is_not_echoed_back():
    # 21.10: a malformed login answered 422 with the submitted password in `input`.
    import asyncio
    import json

    from fastapi.exceptions import RequestValidationError

    from app.main import validation_error_without_input

    error = RequestValidationError([{"type": "string_type", "loc": ("body", "login"), "msg": "Input should be a valid string",
                                     "input": {"login": ["x"], "password": "Secret-Echo-123"}}])
    response = asyncio.run(validation_error_without_input(None, error))

    assert response.status_code == 422
    assert "Secret-Echo-123" not in response.body.decode()
    assert json.loads(response.body)["detail"] == [{"type": "string_type", "loc": ["body", "login"], "msg": "Input should be a valid string"}]
