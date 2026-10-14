from pathlib import Path

import pytest

from material_allocation_copilot.config import AppConfig, DeploymentEnvironment
from material_allocation_copilot.errors import ApplicationError, ErrorCode


def test_application_error_has_stable_envelope_and_read_only_context() -> None:
    error = ApplicationError(
        ErrorCode.INPUT_INVALID,
        "The input batch is invalid.",
        context={"batch_id": "B-01"},
    )

    assert error.to_dict() == {
        "code": "input_invalid",
        "message": "The input batch is invalid.",
        "context": {"batch_id": "B-01"},
    }
    with pytest.raises(TypeError):
        error.context["batch_id"] = "changed"  # type: ignore[index]


def test_config_defaults_are_safe() -> None:
    config = AppConfig.from_env({})

    assert config.environment is DeploymentEnvironment.DEVELOPMENT
    assert config.strict_rule_mode is True
    assert config.treetrace_source_root is None
    assert config.database_url.endswith("material_allocation-development.db")


def test_config_reads_explicit_values() -> None:
    config = AppConfig.from_env(
        {
            "MAC_ENVIRONMENT": "test",
            "MAC_LOG_LEVEL": "debug",
            "MAC_STRICT_RULE_MODE": "false",
            "MAC_TREETRACE_SOURCE_ROOT": "D:/readonly/treetrace",
            "MAC_TEST_DATABASE_URL": "sqlite:///./isolated-test.db",
        }
    )

    assert config.environment is DeploymentEnvironment.TEST
    assert config.log_level == "DEBUG"
    assert config.strict_rule_mode is False
    assert config.treetrace_source_root is not None
    assert config.database_url == "sqlite:///./isolated-test.db"


def test_database_defaults_are_isolated_by_deployment_environment() -> None:
    urls = {
        AppConfig.from_env({"MAC_ENVIRONMENT": environment.value}).database_url
        for environment in DeploymentEnvironment
    }

    assert len(urls) == len(DeploymentEnvironment)


def test_direct_test_config_does_not_inherit_development_database() -> None:
    config = AppConfig(environment=DeploymentEnvironment.TEST)
    assert config.database_url.endswith("material_allocation-test.db")


def test_pilot_runtime_requires_postgresql_psycopg() -> None:
    config = AppConfig(environment=DeploymentEnvironment.PILOT)
    with pytest.raises(ApplicationError) as captured:
        config.validate_runtime_database()
    assert captured.value.code is ErrorCode.CONFIGURATION_ERROR

    AppConfig(
        environment=DeploymentEnvironment.PILOT,
        database_url="postgresql+psycopg://pilot:secret@db/material_allocation",
    ).validate_runtime_database()


def test_config_rejects_ambiguous_boolean() -> None:
    with pytest.raises(ApplicationError) as captured:
        AppConfig.from_env({"MAC_STRICT_RULE_MODE": "sometimes"})

    assert captured.value.code is ErrorCode.CONFIGURATION_ERROR


def test_requirement_model_and_api_key_must_be_configured_together() -> None:
    with pytest.raises(ApplicationError) as captured:
        AppConfig.from_env({"MAC_REQUIREMENT_MODEL": "configured-model"})
    assert captured.value.code is ErrorCode.CONFIGURATION_ERROR

    config = AppConfig.from_env(
        {
            "MAC_REQUIREMENT_MODEL": "configured-model",
            "MAC_OPENAI_API_KEY": "test-key",
        }
    )
    assert config.requirement_model == "configured-model"
    assert config.openai_api_key == "test-key"
    assert "test-key" not in repr(config)


def test_config_reads_database_and_api_key_from_secret_files(tmp_path: Path) -> None:
    database_secret = tmp_path / "database-url"
    api_secret = tmp_path / "openai-key"
    database_secret.write_text("sqlite:///./secret-test.db\n", encoding="utf-8")
    api_secret.write_text("secret-test-key\n", encoding="utf-8")

    config = AppConfig.from_env(
        {
            "MAC_ENVIRONMENT": "test",
            "MAC_TEST_DATABASE_URL_FILE": str(database_secret),
            "MAC_OPENAI_API_KEY_FILE": str(api_secret),
            "MAC_REQUIREMENT_MODEL": "configured-model",
        }
    )

    assert config.database_url == "sqlite:///./secret-test.db"
    assert config.openai_api_key == "secret-test-key"
    with pytest.raises(ApplicationError):
        AppConfig.from_env(
            {
                "MAC_ENVIRONMENT": "test",
                "MAC_TEST_DATABASE_URL": "sqlite:///./direct.db",
                "MAC_TEST_DATABASE_URL_FILE": str(database_secret),
            }
        )


def test_agent_model_requires_explicit_non_negative_token_prices() -> None:
    with pytest.raises(ApplicationError) as captured:
        AppConfig.from_env(
            {"MAC_AGENT_MODEL": "configured-agent", "MAC_OPENAI_API_KEY": "test-key"}
        )
    assert captured.value.code is ErrorCode.CONFIGURATION_ERROR

    with pytest.raises(ApplicationError) as non_finite:
        AppConfig.from_env(
            {
                "MAC_AGENT_MODEL": "configured-agent",
                "MAC_OPENAI_API_KEY": "test-key",
                "MAC_AGENT_INPUT_COST_PER_MILLION_USD": "NaN",
                "MAC_AGENT_OUTPUT_COST_PER_MILLION_USD": "10",
            }
        )
    assert non_finite.value.code is ErrorCode.CONFIGURATION_ERROR

    config = AppConfig.from_env(
        {
            "MAC_AGENT_MODEL": "configured-agent",
            "MAC_OPENAI_API_KEY": "test-key",
            "MAC_AGENT_INPUT_COST_PER_MILLION_USD": "2.5",
            "MAC_AGENT_OUTPUT_COST_PER_MILLION_USD": "10",
        }
    )
    assert config.agent_model == "configured-agent"
    assert str(config.agent_input_cost_per_million_usd) == "2.5"
    assert str(config.agent_output_cost_per_million_usd) == "10"
