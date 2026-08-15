"""Process logging.

Development keeps a readable line format. Production writes one JSON object per
line so a log shipper can index ``run_id`` / ``user_id`` / ``request_id``
without parsing prose. Query strings are never logged: the OAuth callback
carries a one-time code in the query, and an access log that printed it would
print a credential.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .settings import ApiSettings

_EXTRA_FIELDS = ("request_id", "user_id", "run_id", "method", "path", "status", "ms")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _EXTRA_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: ApiSettings) -> None:
    """Install the production formatter. A no-op anywhere else.

    Tests and local uvicorn attach their own handlers; clearing the root logger
    here would silence pytest's capture.
    """
    if not settings.is_production:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Uvicorn's access log includes the query string. Ours does not.
    logging.getLogger("uvicorn.access").disabled = True
