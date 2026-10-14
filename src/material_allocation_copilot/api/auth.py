"""Server-provisioned bearer credentials; callers cannot choose their roles."""

import hashlib
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from material_allocation_copilot.application.access import Principal
from material_allocation_copilot.domain.types import Sha256Hex
from material_allocation_copilot.errors import ApplicationError, ErrorCode


class CredentialEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token_sha256: Sha256Hex = Field(repr=False)
    principal: Principal


class CredentialStore:
    def __init__(self, entries: tuple[CredentialEntry, ...]) -> None:
        self._principals = {entry.token_sha256: entry.principal for entry in entries}
        if len(self._principals) != len(entries):
            raise ApplicationError(ErrorCode.CONFIGURATION_ERROR, "Duplicate API token identities.")

    @classmethod
    def from_env(cls) -> "CredentialStore":
        raw = os.environ.get("MAC_API_PRINCIPALS_JSON", "").strip()
        secret_path = os.environ.get("MAC_API_PRINCIPALS_FILE", "").strip()
        if raw and secret_path:
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "Configure only one of MAC_API_PRINCIPALS_JSON or MAC_API_PRINCIPALS_FILE.",
            )
        if secret_path:
            try:
                raw = Path(secret_path).read_text(encoding="utf-8").strip()
            except OSError as error:
                raise ApplicationError(
                    ErrorCode.CONFIGURATION_ERROR,
                    "MAC_API_PRINCIPALS_FILE could not be read.",
                ) from error
        raw = raw or "[]"
        try:
            entries = TypeAdapter(tuple[CredentialEntry, ...]).validate_python(json.loads(raw))
        except (ValueError, ValidationError) as error:
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR, "MAC_API_PRINCIPALS_JSON is invalid."
            ) from error
        return cls(entries)

    @property
    def is_empty(self) -> bool:
        return not self._principals

    def authenticate(self, token: str) -> Principal:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        principal = self._principals.get(digest)
        if principal is None:
            raise ApplicationError(ErrorCode.UNAUTHENTICATED, "A valid bearer token is required.")
        return principal


bearer = HTTPBearer(auto_error=False)


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None:
        raise ApplicationError(ErrorCode.UNAUTHENTICATED, "A valid bearer token is required.")
    return request.app.state.credentials.authenticate(credentials.credentials)
