"""FastAPI composition root. Starting the server never runs migrations."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from material_allocation_copilot.adapters.openai_agent import OpenAIAgentModel
from material_allocation_copilot.adapters.openai_requirements import OpenAIRequirementModel
from material_allocation_copilot.api.auth import CredentialStore
from material_allocation_copilot.api.middleware import RequestMiddleware, error_response
from material_allocation_copilot.api.routes import router
from material_allocation_copilot.api.schemas import (
    HealthResponse,
    MetricsResponse,
    ReadinessResponse,
)
from material_allocation_copilot.application.agent_runtime import AgentModel, UnavailableAgentModel
from material_allocation_copilot.application.semantic_requirements import (
    StructuredRequirementModel,
    UnavailableRequirementModel,
)
from material_allocation_copilot.application.services import UnitOfWorkFactory
from material_allocation_copilot.application.workflow import WorkflowService
from material_allocation_copilot.config import AppConfig, DeploymentEnvironment
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.observability import OperationalMetrics, configure_json_logging
from material_allocation_copilot.persistence import (
    SqlAlchemyUnitOfWork,
    create_db_engine,
    create_session_factory,
)
from material_allocation_copilot.persistence.bootstrap import verify_schema

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
logger = logging.getLogger(__name__)


def create_app(
    config: AppConfig | None = None,
    *,
    uow_factory: UnitOfWorkFactory | None = None,
    credentials: CredentialStore | None = None,
    requirement_model: StructuredRequirementModel | None = None,
    agent_model: AgentModel | None = None,
) -> FastAPI:
    settings = config or AppConfig.from_env()
    settings.validate_runtime_database()
    engine = None
    if uow_factory is None:
        engine = create_db_engine(settings.database_url)
        session_factory = create_session_factory(engine)

        def default_uow() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        uow_factory = default_uow

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            configure_json_logging(settings.log_level)
            if engine is not None:
                verify_schema(engine)
            recovered = app.state.workflow.recovery.recover_interrupted()
            logger.info(
                "startup_recovery_complete",
                extra={"event": "startup_recovery_complete"},
            )
            if any(recovered.values()):
                logger.warning(
                    "interrupted_work_marked_failed tasks=%d agent_runs=%d",
                    recovered["tasks_failed"],
                    recovered["agent_runs_failed"],
                    extra={"event": "interrupted_work_marked_failed"},
                )
            yield
        finally:
            if engine is not None:
                engine.dispose()

    openai_client = None
    if settings.openai_api_key and (settings.requirement_model or settings.agent_model):
        from openai import OpenAI

        openai_client = OpenAI(api_key=settings.openai_api_key)
    if requirement_model is None:
        if openai_client is not None and settings.requirement_model:
            requirement_model = OpenAIRequirementModel(openai_client, settings.requirement_model)
        else:
            requirement_model = UnavailableRequirementModel()
    if agent_model is None:
        if openai_client is not None and settings.agent_model:
            assert settings.agent_input_cost_per_million_usd is not None
            assert settings.agent_output_cost_per_million_usd is not None
            agent_model = OpenAIAgentModel(
                openai_client,
                settings.agent_model,
                input_cost_per_million_usd=settings.agent_input_cost_per_million_usd,
                output_cost_per_million_usd=settings.agent_output_cost_per_million_usd,
            )
        else:
            agent_model = UnavailableAgentModel()

    configured_credentials = credentials if credentials is not None else CredentialStore.from_env()
    if settings.environment is DeploymentEnvironment.PILOT and configured_credentials.is_empty:
        raise ApplicationError(
            ErrorCode.CONFIGURATION_ERROR,
            "Pilot mode requires at least one server-provisioned API principal.",
        )

    app = FastAPI(
        title="Material Allocation Copilot",
        version="1.4.0",
        lifespan=lifespan,
        description=(
            "Deterministic candidate allocation and review. Exports are not execution orders."
        ),
    )
    app.state.config = settings
    app.state.credentials = configured_credentials
    app.state.engine = engine
    app.state.metrics = OperationalMetrics()
    app.state.workflow = WorkflowService(
        uow_factory, requirement_model=requirement_model, agent_model=agent_model
    )
    app.add_middleware(RequestMiddleware)

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, error: ApplicationError):
        statuses = {
            ErrorCode.CONFIGURATION_ERROR: 503,
            ErrorCode.INPUT_INVALID: 422,
            ErrorCode.DOMAIN_VALIDATION_ERROR: 422,
            ErrorCode.NOT_FOUND: 404,
            ErrorCode.CONFLICT: 409,
            ErrorCode.NOT_EVALUABLE: 422,
            ErrorCode.UNAUTHENTICATED: 401,
            ErrorCode.FORBIDDEN: 403,
            ErrorCode.PAYLOAD_TOO_LARGE: 413,
            ErrorCode.MODEL_UNAVAILABLE: 503,
            ErrorCode.MODEL_INVOCATION_FAILED: 502,
        }
        status = statuses.get(error.code, 500)
        public_error = (
            error if status != 500 else ApplicationError(error.code, "Service unavailable.")
        )
        return error_response(public_error, request.state.request_id, status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        details = [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return error_response(
            ApplicationError(
                ErrorCode.INPUT_INVALID,
                "Request does not satisfy its contract.",
                context={"errors": details},
            ),
            request.state.request_id,
            422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        code = ErrorCode.NOT_FOUND if error.status_code == 404 else ErrorCode.INPUT_INVALID
        return error_response(
            ApplicationError(code, "HTTP operation failed."),
            request.state.request_id,
            error.status_code,
        )

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    def health():
        return HealthResponse()

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    def liveness():
        return HealthResponse()

    @app.get("/health/ready", response_model=ReadinessResponse, tags=["health"])
    def readiness():
        if engine is not None:
            verify_schema(engine)
            return ReadinessResponse(database="ready")
        return ReadinessResponse(database="externally_managed")

    @app.get("/metrics", response_model=MetricsResponse, tags=["operations"])
    def metrics():
        return app.state.metrics.snapshot()

    @app.get("/", include_in_schema=False)
    def simple_workflow():
        return FileResponse(
            WEB_ROOT / "simple.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    app.include_router(router)
    app.mount("/simple/assets", StaticFiles(directory=WEB_ROOT), name="simple-assets")
    return app
