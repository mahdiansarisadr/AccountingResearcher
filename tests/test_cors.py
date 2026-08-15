"""CORS: the frontend is a different origin and must be named, not wildcarded."""

from __future__ import annotations


def test_the_frontend_origin_is_allowed_to_call_the_api(anonymous) -> None:
    response = anonymous.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_an_unknown_origin_is_not_reflected(anonymous) -> None:
    response = anonymous.get("/health", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers
