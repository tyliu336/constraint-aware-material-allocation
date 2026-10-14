"""Trusted identities and minimal role/project access for application entry points."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from material_allocation_copilot.domain.types import Identifier
from material_allocation_copilot.errors import ApplicationError, ErrorCode


class Role(StrEnum):
    READER = "reader"
    PLANNER = "planner"
    REVIEWER = "reviewer"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: Identifier
    roles: frozenset[Role] = Field(min_length=1)
    project_ids: frozenset[Identifier] = Field(min_length=1)

    def require_project(self, project_id: str, role: Role | None = None) -> None:
        if project_id not in self.project_ids or (role is not None and role not in self.roles):
            raise ApplicationError(ErrorCode.FORBIDDEN, "This operation is not permitted.")
