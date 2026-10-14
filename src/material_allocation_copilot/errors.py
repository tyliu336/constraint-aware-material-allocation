"""Stable application error envelope used across future CLI, API, and tools."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ErrorCode(StrEnum):
    CONFIGURATION_ERROR = "configuration_error"
    DOMAIN_VALIDATION_ERROR = "domain_validation_error"
    INPUT_INVALID = "input_invalid"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    NOT_EVALUABLE = "not_evaluable"
    INTERNAL_ERROR = "internal_error"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_INVOCATION_FAILED = "model_invocation_failed"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_RESULT_TOO_LARGE = "tool_result_too_large"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"


class ApplicationError(Exception):
    """Expected, structured failure safe to translate at an application boundary."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = MappingProxyType(dict(context or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "context": dict(self.context),
        }
