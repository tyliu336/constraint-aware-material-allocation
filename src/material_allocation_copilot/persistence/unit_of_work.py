"""SQLAlchemy unit of work with explicit begin/commit/rollback."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.persistence.repositories import (
    SqlAlchemyAgentRunRepository,
    SqlAlchemyApprovalRepository,
    SqlAlchemyContextSnapshotRepository,
    SqlAlchemyInventoryRepository,
    SqlAlchemyPlanRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemyRequirementDraftRepository,
    SqlAlchemyRequirementRepository,
    SqlAlchemyReservationRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyToolCallLogRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        if self._session is not None:
            raise RuntimeError("unit of work is already active")
        self._session = self._session_factory()
        self._session.begin()
        self.projects = SqlAlchemyProjectRepository(self._session)
        self.inventories = SqlAlchemyInventoryRepository(self._session)
        self.requirements = SqlAlchemyRequirementRepository(self._session)
        self.requirement_drafts = SqlAlchemyRequirementDraftRepository(self._session)
        self.context_snapshots = SqlAlchemyContextSnapshotRepository(self._session)
        self.tool_calls = SqlAlchemyToolCallLogRepository(self._session)
        self.agent_runs = SqlAlchemyAgentRunRepository(self._session)
        self.tasks = SqlAlchemyTaskRepository(self._session)
        self.plans = SqlAlchemyPlanRepository(self._session)
        self.approvals = SqlAlchemyApprovalRepository(self._session)
        self.reservations = SqlAlchemyReservationRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        try:
            if self._session.in_transaction():
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
        if isinstance(exc_value, IntegrityError):
            raise ApplicationError(
                ErrorCode.CONFLICT, "The write conflicts with database integrity constraints."
            ) from exc_value

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.rollback()
