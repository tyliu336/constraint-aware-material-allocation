"""Single entry point for process configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from material_allocation_copilot.errors import ApplicationError, ErrorCode


class DeploymentEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PILOT = "pilot"


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _secret_value(values: Mapping[str, str], direct_name: str, file_name: str) -> str | None:
    direct = values.get(direct_name, "").strip()
    secret_path = values.get(file_name, "").strip()
    if direct and secret_path:
        raise ApplicationError(
            ErrorCode.CONFIGURATION_ERROR,
            f"Configure only one of {direct_name} or {file_name}.",
        )
    if not secret_path:
        return direct or None
    path = Path(secret_path).expanduser()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ApplicationError(
            ErrorCode.CONFIGURATION_ERROR,
            f"{file_name} could not be read.",
            context={"variable_name": file_name},
        ) from error
    if not value:
        raise ApplicationError(
            ErrorCode.CONFIGURATION_ERROR,
            f"{file_name} points to an empty secret.",
            context={"variable_name": file_name},
        )
    return value


def _parse_bool(raw_value: str, *, variable_name: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ApplicationError(
        ErrorCode.CONFIGURATION_ERROR,
        f"{variable_name} must be one of {sorted(_TRUE_VALUES | _FALSE_VALUES)}.",
        context={"variable_name": variable_name},
    )


@dataclass(frozen=True, slots=True)
class AppConfig:
    environment: DeploymentEnvironment = DeploymentEnvironment.DEVELOPMENT
    log_level: str = "INFO"
    strict_rule_mode: bool = True
    treetrace_source_root: Path | None = None
    database_url: str = ""
    openai_api_key: str | None = field(default=None, repr=False)
    requirement_model: str | None = None
    agent_model: str | None = None
    agent_input_cost_per_million_usd: Decimal | None = None
    agent_output_cost_per_million_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.database_url:
            object.__setattr__(
                self, "database_url", f"sqlite:///./material_allocation-{self.environment.value}.db"
            )
        configured_models = bool(self.requirement_model or self.agent_model)
        if bool(self.openai_api_key) != configured_models:
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "MAC_OPENAI_API_KEY must be configured with at least one model, and vice versa.",
            )
        agent_rates = (
            self.agent_input_cost_per_million_usd,
            self.agent_output_cost_per_million_usd,
        )
        if self.agent_model and (
            None in agent_rates
            or any(not rate.is_finite() or rate < 0 for rate in agent_rates if rate is not None)
        ):
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "Agent input and output token prices must be configured as non-negative values.",
            )
        if not self.agent_model and any(rate is not None for rate in agent_rates):
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "Agent token prices require MAC_AGENT_MODEL.",
            )

    def validate_runtime_database(self) -> None:
        if self.environment is DeploymentEnvironment.PILOT and not self.database_url.startswith(
            "postgresql+psycopg://"
        ):
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "Pilot mode requires a postgresql+psycopg database URL.",
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AppConfig:
        values = os.environ if environ is None else environ
        environment_raw = values.get("MAC_ENVIRONMENT", DeploymentEnvironment.DEVELOPMENT.value)
        log_level = values.get("MAC_LOG_LEVEL", "INFO").strip().upper()

        try:
            environment = DeploymentEnvironment(environment_raw.strip().lower())
        except ValueError as error:
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "MAC_ENVIRONMENT has an unsupported value.",
                context={"value": environment_raw},
            ) from error

        if log_level not in _LOG_LEVELS:
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "MAC_LOG_LEVEL has an unsupported value.",
                context={"value": log_level},
            )

        strict_rule_mode = _parse_bool(
            values.get("MAC_STRICT_RULE_MODE", "true"),
            variable_name="MAC_STRICT_RULE_MODE",
        )
        source_root_raw = values.get("MAC_TREETRACE_SOURCE_ROOT")
        source_root = Path(source_root_raw).expanduser() if source_root_raw else None
        database_variable = f"MAC_{environment.value.upper()}_DATABASE_URL"
        database_url = (
            _secret_value(values, database_variable, f"{database_variable}_FILE")
            or f"sqlite:///./material_allocation-{environment.value}.db"
        )
        if not database_url:
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                f"{database_variable} must not be blank.",
                context={"variable_name": database_variable},
            )
        openai_api_key = _secret_value(values, "MAC_OPENAI_API_KEY", "MAC_OPENAI_API_KEY_FILE")
        requirement_model = values.get("MAC_REQUIREMENT_MODEL", "").strip() or None
        agent_model = values.get("MAC_AGENT_MODEL", "").strip() or None

        def decimal_value(name: str) -> Decimal | None:
            raw = values.get(name, "").strip()
            if not raw:
                return None
            try:
                return Decimal(raw)
            except InvalidOperation as error:
                raise ApplicationError(
                    ErrorCode.CONFIGURATION_ERROR,
                    f"{name} must be a decimal number.",
                    context={"variable_name": name},
                ) from error

        return cls(
            environment=environment,
            log_level=log_level,
            strict_rule_mode=strict_rule_mode,
            treetrace_source_root=source_root,
            database_url=database_url,
            openai_api_key=openai_api_key,
            requirement_model=requirement_model,
            agent_model=agent_model,
            agent_input_cost_per_million_usd=decimal_value("MAC_AGENT_INPUT_COST_PER_MILLION_USD"),
            agent_output_cost_per_million_usd=decimal_value(
                "MAC_AGENT_OUTPUT_COST_PER_MILLION_USD"
            ),
        )
