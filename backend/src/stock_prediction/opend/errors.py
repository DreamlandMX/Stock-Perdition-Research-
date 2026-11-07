"""Stable, non-sensitive OpenD error classifications."""

from dataclasses import dataclass
from enum import StrEnum


class OpenDErrorClass(StrEnum):
    """Error categories that remain stable across futu SDK message changes."""

    CONNECTION_FAILED = "connection_failed"
    NOT_LOGGED_IN = "not_logged_in"
    PERMISSION_DENIED = "permission_denied"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    PARTIAL_RESPONSE = "partial_response"
    SDK_ERROR = "sdk_error"


@dataclass(frozen=True, slots=True)
class OpenDError:
    """Sanitized error returned by the adapter."""

    error_class: OpenDErrorClass
    message: str
    retryable: bool


_STABLE_MESSAGES = {
    OpenDErrorClass.CONNECTION_FAILED: "OpenD connection failed",
    OpenDErrorClass.NOT_LOGGED_IN: "OpenD quote service is not logged in",
    OpenDErrorClass.PERMISSION_DENIED: "OpenD permission denied",
    OpenDErrorClass.QUOTA_EXHAUSTED: "OpenD quota exhausted",
    OpenDErrorClass.RATE_LIMITED: "OpenD request rate limited",
    OpenDErrorClass.PARTIAL_RESPONSE: "OpenD returned a partial response",
    OpenDErrorClass.SDK_ERROR: "OpenD SDK request failed",
}


def classify_sdk_error(raw_message: str) -> OpenDError:
    """Classify an SDK message without retaining or exposing the raw response."""

    normalized = raw_message.casefold()
    if any(
        marker in normalized
        for marker in (
            "connection",
            "connect failed",
            "connect timeout",
            "socket",
            "broken pipe",
            "refused",
            "opend is not running",
        )
    ):
        error_class = OpenDErrorClass.CONNECTION_FAILED
    elif any(
        marker in normalized
        for marker in ("not login", "not logged", "login required", "login first")
    ):
        error_class = OpenDErrorClass.NOT_LOGGED_IN
    elif any(
        marker in normalized
        for marker in ("permission", "no right", "quote right", "authority")
    ):
        error_class = OpenDErrorClass.PERMISSION_DENIED
    elif "quota" in normalized and any(
        marker in normalized
        for marker in ("exhaust", "insufficient", "not enough", "no remain")
    ):
        error_class = OpenDErrorClass.QUOTA_EXHAUSTED
    elif any(
        marker in normalized
        for marker in (
            "rate limit",
            "too frequent",
            "frequency",
            "too many request",
            "频率",
            "频繁",
        )
    ):
        error_class = OpenDErrorClass.RATE_LIMITED
    elif any(marker in normalized for marker in ("partial", "incomplete")):
        error_class = OpenDErrorClass.PARTIAL_RESPONSE
    else:
        error_class = OpenDErrorClass.SDK_ERROR

    retryable = error_class in {
        OpenDErrorClass.CONNECTION_FAILED,
        OpenDErrorClass.RATE_LIMITED,
        OpenDErrorClass.PARTIAL_RESPONSE,
        OpenDErrorClass.SDK_ERROR,
    }
    return OpenDError(error_class, _STABLE_MESSAGES[error_class], retryable)


def classify_sdk_exception(exception: Exception) -> OpenDError:
    """Classify an SDK exception without retaining its message or arguments."""

    if isinstance(exception, PermissionError):
        error_class = OpenDErrorClass.PERMISSION_DENIED
        return OpenDError(error_class, _STABLE_MESSAGES[error_class], retryable=False)
    if isinstance(exception, (ConnectionError, TimeoutError, OSError)):
        return connection_error()
    return classify_sdk_error(str(exception))


def connection_error() -> OpenDError:
    """Return the stable error used when context creation fails."""

    error_class = OpenDErrorClass.CONNECTION_FAILED
    return OpenDError(error_class, _STABLE_MESSAGES[error_class], retryable=True)


def partial_response_error() -> OpenDError:
    """Return the stable error used for malformed successful SDK responses."""

    error_class = OpenDErrorClass.PARTIAL_RESPONSE
    return OpenDError(error_class, _STABLE_MESSAGES[error_class], retryable=True)
