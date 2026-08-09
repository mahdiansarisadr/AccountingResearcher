"""Liveness and readiness endpoints.

Liveness (`/health`) answers "is this process running?" and must stay cheap and
dependency-free — an orchestrator uses it to decide whether to RESTART.
Readiness (`/ready`) answers "can I serve traffic?" by probing dependencies —
an orchestrator uses it to decide whether to SEND TRAFFIC.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from ..checks import check_postgres, check_redis

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(response: Response) -> dict[str, object]:
    probes = {"postgres": check_postgres(), "redis": check_redis()}
    all_ok = all(probe.ok for probe in probes.values())

    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if all_ok else "not_ready",
        "checks": {
            name: {"ok": probe.ok, "detail": probe.detail}
            for name, probe in probes.items()
        },
    }
