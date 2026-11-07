"""Expected API failures that are safe to serialize for local clients."""

from __future__ import annotations


class ApiError(Exception):
    """An expected API failure with a stable machine-readable error code."""

    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        message: str,
        retryable: bool,
        affected_scope: list[str],
    ) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        self.affected_scope = affected_scope
        super().__init__(message)


__all__ = ["ApiError"]
