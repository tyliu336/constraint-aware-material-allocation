"""MCP composition root backed only by authorized application services."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from mcp import stdio_server
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    CallToolRequestParams,
    CallToolResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    TextContent,
    TextResourceContents,
    Tool,
    ToolAnnotations,
)

from material_allocation_copilot.api.auth import CredentialStore
from material_allocation_copilot.application.access import Principal
from material_allocation_copilot.application.queries import QueryService
from material_allocation_copilot.application.services import UnitOfWorkFactory
from material_allocation_copilot.application.tooling import (
    TOOL_REGISTRY_VERSION,
    ToolExecutionResult,
    ToolExecutor,
)
from material_allocation_copilot.config import AppConfig
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.persistence import (
    SqlAlchemyUnitOfWork,
    create_db_engine,
    create_session_factory,
)
from material_allocation_copilot.persistence.bootstrap import verify_schema
from material_allocation_copilot.rules import default_rule_registry

MCP_SERVER_VERSION = "1.4.0"
MCP_RESOURCE_LIMIT_BYTES = 512_000
MCP_TOOL_RESULT_LIMIT_BYTES = 1_000_000
RULE_CATALOG_URI = "material-allocation://rule-catalog"
TOOL_SCHEMAS_URI = "material-allocation://tool-schemas"
PROJECT_SUMMARY_TEMPLATE = "material-allocation://projects/{project_id}/summary"
RUN_SUMMARY_TEMPLATE = "material-allocation://projects/{project_id}/runs/{run_id}"


@dataclass(frozen=True, slots=True)
class MCPBinding:
    """One local server process is bound to one trusted identity and project."""

    principal: Principal
    project_id: str
    allowed_tools: frozenset[str] | None = None

    def __post_init__(self) -> None:
        self.principal.require_project(self.project_id)

    @classmethod
    def from_env(cls, credentials: CredentialStore) -> MCPBinding:
        token = os.environ.get("MAC_MCP_BEARER_TOKEN", "").strip()
        project_id = os.environ.get("MAC_MCP_PROJECT_ID", "").strip()
        if not token or not project_id:
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "MAC_MCP_BEARER_TOKEN and MAC_MCP_PROJECT_ID are required.",
            )
        raw_tools = os.environ.get("MAC_MCP_ALLOWED_TOOLS", "").strip()
        if not raw_tools:
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "MAC_MCP_ALLOWED_TOOLS is required for explicit tool permission binding.",
            )
        try:
            parsed_tools = json.loads(raw_tools)
        except ValueError as error:
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "MAC_MCP_ALLOWED_TOOLS must be a JSON array of tool names.",
            ) from error
        if (
            not isinstance(parsed_tools, list)
            or not parsed_tools
            or any(not isinstance(item, str) or not item.strip() for item in parsed_tools)
        ):
            raise ApplicationError(
                ErrorCode.CONFIGURATION_ERROR,
                "MAC_MCP_ALLOWED_TOOLS must be a non-empty JSON array of tool names.",
            )
        return cls(
            credentials.authenticate(token),
            project_id,
            frozenset(item.strip() for item in parsed_tools),
        )


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _json_text(value: object, *, maximum_bytes: int = MCP_RESOURCE_LIMIT_BYTES) -> str:
    encoded = json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise MCPError(INTERNAL_ERROR, "MCP result exceeds its declared output limit.")
    return encoded


def _protocol_error(error: ApplicationError) -> MCPError:
    return MCPError(
        INVALID_PARAMS,
        error.message,
        {"application_error_code": error.code.value},
    )


def _resource_parts(uri: str) -> tuple[str, ...]:
    parsed = urlsplit(uri)
    if parsed.scheme != "material-allocation" or parsed.query or parsed.fragment:
        raise MCPError(INVALID_PARAMS, "Unsupported resource URI.")
    return tuple(part for part in (parsed.netloc, *parsed.path.split("/")) if part)


def create_mcp_server(
    uow_factory: UnitOfWorkFactory,
    binding: MCPBinding,
    *,
    run_id: str | None = None,
) -> Server[Any]:
    """Create a protocol server without exposing persistence to the MCP layer."""

    executor = ToolExecutor(uow_factory)
    queries = QueryService(uow_factory)
    queries.get_project(binding.principal, binding.project_id)
    definitions = {item.name: item for item in executor.registry.list()}
    allowed_tools = binding.allowed_tools or frozenset(
        name
        for name, definition in definitions.items()
        if definition.required_role is None or definition.required_role in binding.principal.roles
    )
    unknown_tools = allowed_tools - definitions.keys()
    unauthorized_tools = {
        name
        for name in allowed_tools
        if name in definitions
        and definitions[name].required_role is not None
        and definitions[name].required_role not in binding.principal.roles
    }
    if unknown_tools or unauthorized_tools:
        raise ApplicationError(
            ErrorCode.CONFIGURATION_ERROR,
            "MCP tool permissions contain unknown or role-incompatible tools.",
            context={
                "unknown_tools": sorted(unknown_tools),
                "role_incompatible_tools": sorted(unauthorized_tools),
            },
        )
    server_run_id = run_id or f"MCP-RUN-{uuid4().hex}"
    annotations = ToolAnnotations(
        title="Read-only material allocation analysis",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    async def list_tools(_context: Any, _params: Any) -> ListToolsResult:
        tools = [
            Tool(
                name=definition.name,
                description=definition.description,
                inputSchema=definition.input_model.model_json_schema(),
                outputSchema=ToolExecutionResult.model_json_schema(),
                annotations=annotations,
                _meta={
                    "applicationToolVersion": definition.version,
                    "registryVersion": TOOL_REGISTRY_VERSION,
                    "maximumResultBytes": definition.max_result_bytes,
                    "timeoutMilliseconds": definition.timeout_ms,
                },
            )
            for definition in executor.registry.list()
            if definition.name in allowed_tools
        ]
        return ListToolsResult(tools=tools)

    async def call_tool(
        context: ServerRequestContext[Any, Any], params: CallToolRequestParams
    ) -> CallToolResult:
        client_params = context.session.client_params
        client_info = client_params.client_info if client_params is not None else None
        result = executor.execute(
            binding.principal,
            project_id=binding.project_id,
            run_id=server_run_id,
            tool_call_id=f"MCP-CALL-{uuid4().hex}",
            tool_name=params.name,
            arguments=params.arguments or {},
            allowed_tools=allowed_tools,
            audit_context={
                "protocol": "mcp",
                "transport": "stdio_or_in_memory",
                "client_name": client_info.name if client_info is not None else "unknown",
                "client_version": client_info.version if client_info is not None else "unknown",
                "approval_policy": "operator_tool_allowlist",
                "approval_status": "preapproved_read_only",
            },
        )
        payload = result.model_dump(mode="json")
        return CallToolResult(
            content=[
                TextContent(text=_json_text(payload, maximum_bytes=MCP_TOOL_RESULT_LIMIT_BYTES))
            ],
            structuredContent=payload,
            isError=result.status != "succeeded",
        )

    async def list_resources(_context: Any, _params: Any) -> ListResourcesResult:
        return ListResourcesResult(
            resources=[
                Resource(
                    name="rule_catalog",
                    title="Deterministic rule catalog",
                    uri=RULE_CATALOG_URI,
                    description="Versioned hard-rule identities exposed as read-only context.",
                    mimeType="application/json",
                ),
                Resource(
                    name="tool_schemas",
                    title="Application tool schemas",
                    uri=TOOL_SCHEMAS_URI,
                    description="Schemas from the same registry used by API agent tools.",
                    mimeType="application/json",
                ),
            ]
        )

    async def list_resource_templates(_context: Any, _params: Any) -> ListResourceTemplatesResult:
        return ListResourceTemplatesResult(
            resourceTemplates=[
                ResourceTemplate(
                    name="project_summary",
                    title="Authorized project summary",
                    uriTemplate=PROJECT_SUMMARY_TEMPLATE,
                    description="Summary for the project bound to this MCP process.",
                    mimeType="application/json",
                ),
                ResourceTemplate(
                    name="run_summary",
                    title="Authorized MCP run summary",
                    uriTemplate=RUN_SUMMARY_TEMPLATE,
                    description="Audited tool calls for one run in the bound project.",
                    mimeType="application/json",
                ),
            ]
        )

    async def read_resource(_context: Any, params: ReadResourceRequestParams) -> ReadResourceResult:
        uri = str(params.uri)
        try:
            if uri == RULE_CATALOG_URI:
                registry = default_rule_registry()
                payload: object = {
                    "rule_set_version": registry.rule_set_version,
                    "rules": [
                        {
                            "rule_id": rule.rule_id,
                            "rule_version": rule.rule_version,
                            "scope": scope,
                        }
                        for scope, rules in (
                            ("pair", registry.pair_rules),
                            ("global", registry.global_rules),
                        )
                        for rule in rules
                    ],
                }
            elif uri == TOOL_SCHEMAS_URI:
                payload = {
                    "registry_version": TOOL_REGISTRY_VERSION,
                    "tools": [
                        {
                            "name": definition.name,
                            "version": definition.version,
                            "description": definition.description,
                            "input_schema": definition.input_model.model_json_schema(),
                            "output_schema": definition.output_model.model_json_schema(),
                        }
                        for definition in executor.registry.list()
                    ],
                }
            else:
                parts = _resource_parts(uri)
                if len(parts) == 3 and parts[0] == "projects" and parts[2] == "summary":
                    project_id = parts[1]
                    binding.principal.require_project(project_id)
                    if project_id != binding.project_id:
                        raise ApplicationError(
                            ErrorCode.FORBIDDEN, "This operation is not permitted."
                        )
                    project = queries.get_project(binding.principal, project_id)
                    payload = {
                        "project": asdict(project),
                        "access": {
                            "user_id": binding.principal.user_id,
                            "roles": sorted(role.value for role in binding.principal.roles),
                            "mode": "read_only",
                        },
                        "tool_count": len(executor.registry.list()),
                    }
                elif len(parts) == 4 and parts[0] == "projects" and parts[2] == "runs":
                    project_id, run_id_value = parts[1], parts[3]
                    if project_id != binding.project_id:
                        raise ApplicationError(
                            ErrorCode.FORBIDDEN, "This operation is not permitted."
                        )
                    records = queries.list_tool_run_calls(
                        binding.principal, project_id, run_id_value
                    )
                    payload = {
                        "run_id": run_id_value,
                        "project_id": project_id,
                        "tool_calls": [asdict(record) for record in records],
                    }
                else:
                    raise MCPError(METHOD_NOT_FOUND, "MCP resource was not found.")
        except ApplicationError as error:
            raise _protocol_error(error) from error
        text = _json_text(payload)
        return ReadResourceResult(
            contents=[TextResourceContents(uri=uri, mimeType="application/json", text=text)]
        )

    return Server(
        "material-allocation-copilot",
        version=MCP_SERVER_VERSION,
        title="Material Allocation Copilot",
        description="Authorized read-only material allocation tools and resources.",
        instructions=(
            "Use tools for analysis only. Tool outputs are candidate decisions, never execution "
            "orders. Human approval remains outside this MCP server."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_list_resource_templates=list_resource_templates,
        on_read_resource=read_resource,
    )


async def _serve_stdio(server: Server[Any]) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Run the local stdio server using the same database and credentials as the API."""

    settings = AppConfig.from_env()
    settings.validate_runtime_database()
    engine = create_db_engine(settings.database_url)
    try:
        verify_schema(engine)
        session_factory = create_session_factory(engine)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        credentials = CredentialStore.from_env()
        binding = MCPBinding.from_env(credentials)
        asyncio.run(_serve_stdio(create_mcp_server(uow_factory, binding)))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
