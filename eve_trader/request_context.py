"""Per-request correlation id, shared by the API middleware and the logging
filter. A ContextVar so threads started with ``contextvars.copy_context``
(pipeline_runner, scheduler job workers) inherit the id of the request that
started them. Its own module, with no imports from the rest of the app, so
logging_setup can use it without an import cycle."""
from __future__ import annotations

import contextvars
import re
import uuid
from typing import Optional

# Only ids of this shape are accepted from a client: anything else (control
# characters, newlines, very long values) would let a caller forge log lines.
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex


def accept_or_generate(incoming: Optional[str]) -> str:
    if incoming and REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return new_request_id()
