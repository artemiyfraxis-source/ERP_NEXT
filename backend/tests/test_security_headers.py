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
