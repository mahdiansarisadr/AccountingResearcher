"""Production hardening: docs closed, bodies capped, rate limits, request ids."""

from __future__ import annotations

from api.main import create_app
from fastapi.testclient import TestClient

from .conftest import production_settings


def test_docs_are_open_outside_production(anonymous) -> None:
    assert anonymous.get("/docs").status_code == 200
    assert anonymous.get("/openapi.json").status_code == 200


def test_docs_are_closed_in_production() -> None:
    app = create_app(production_settings())

    with TestClient(app, base_url="https://research.example.test") as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/health").status_code == 200


def test_a_request_is_tagged_with_an_id(anonymous) -> None:
    response = anonymous.get("/health")

    assert response.headers["x-request-id"]
    echoed = anonymous.get(
        "/health", headers={"x-request-id": "client-supplied-id"}
    )
    assert echoed.headers["x-request-id"] == "client-supplied-id"


def test_an_oversized_body_is_refused(anonymous) -> None:
    response = anonymous.post(
        "/auth/dev-login",
        content=b"x" * (65 * 1024),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "request too large"


def test_a_dataset_upload_is_not_capped_like_json() -> None:
    app = create_app(production_settings())
    csv = b"a,b\n" + b"1,2\n" * 20_000
    assert len(csv) > 64 * 1024

    with TestClient(app, base_url="https://research.example.test") as client:
        response = client.post(
            "/threads/00000000-0000-0000-0000-000000000001/files",
            files={"file": ("data.csv", csv, "text/csv")},
        )

    assert response.status_code != 413


def test_requests_over_the_rate_limit_are_refused(api_app, api_settings, redis) -> None:
    capped = api_settings.model_copy(update={"rate_limit_requests": 2, "rate_limit_window_seconds": 60})
    app = create_app(capped)
    app.state.redis_factory = lambda: redis
    app.dependency_overrides.update(api_app.dependency_overrides)

    with TestClient(app) as client:
        assert client.get("/me").status_code == 401
        assert client.get("/me").status_code == 401
        limited = client.get("/me")
        assert limited.status_code == 429
        assert limited.headers["retry-after"]
        # Probes must not consume the budget, or a health checker would lock everyone out.
        assert client.get("/health").status_code == 200
