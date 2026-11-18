"""Structured logging and secret redaction."""

import logging
import re
import sys

# Redact patterns like sk-..., Bearer ..., xi-api-key, tickets, secrets
SENSITIVE_PATTERNS = [
    re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(
        r"((?:xi-api-key|api[_-]?key|ticket|token|secret|password)\s*[:=]\s*)[a-zA-Z0-9_\-\.]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r'("?(?:ticket|api_key|apiKey|token|secret|password)"?\s*:\s*")[^"]+(")',
        re.IGNORECASE,
    ),
]


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        redacted = original
        for pattern in SENSITIVE_PATTERNS:
            if pattern.groups == 2:
                redacted = pattern.sub(r"\g<1>[REDACTED]\g<2>", redacted)
            else:
                redacted = pattern.sub(r"\g<1>[REDACTED]", redacted)
        return redacted


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingFormatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # Replace handlers
    root_logger.handlers = [handler]
