"""Read-only schema compatibility check before serving requests."""

from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.persistence.models import Base


def verify_schema(engine: Engine) -> None:
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            if not set(Base.metadata.tables).issubset(tables) or "alembic_version" not in tables:
                raise ApplicationError(
                    ErrorCode.CONFIGURATION_ERROR,
                    "Run alembic upgrade head before starting the API.",
                )
            revisions = connection.scalars(text("SELECT version_num FROM alembic_version")).all()
            if revisions != ["20261011_0006"]:
                raise ApplicationError(
                    ErrorCode.CONFIGURATION_ERROR, "Database schema revision is unsupported."
                )
    except SQLAlchemyError as error:
        raise ApplicationError(
            ErrorCode.CONFIGURATION_ERROR, "Database schema could not be checked."
        ) from error
