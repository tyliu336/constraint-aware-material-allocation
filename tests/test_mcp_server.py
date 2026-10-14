from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from mcp import Client, MCPError, StdioServerParameters
from mcp.types import Implementation
from sqlalchemy import text

from material_allocation_copilot.api.auth import CredentialEntry
from material_allocation_copilot.application import (
    ConfirmedRequirementInput,
    InventoryImportInput,
    InventoryService,
    ProjectService,
    RequirementService,
    TaskService,
)
from material_allocation_copilot.application.access import Principal, Role
from material_allocation_copilot.application.queries import QueryService
from material_allocation_copilot.application.tooling import default_tool_registry
from material_allocation_copilot.domain import (
    AssessmentMethod,
    AvailabilityStatus,
    DefectDataStatus,
    MaterialItem,
    OrientationRequirement,
    SlotRequirement,
    StructuralUseStatus,
)
from material_allocation_copilot.mcp_server import MCPBinding, create_mcp_server
from material_allocation_copilot.mcp_server.server import (
    PROJECT_SUMMARY_TEMPLATE,
    RULE_CATALOG_URI,
    RUN_SUMMARY_TEMPLATE,
    TOOL_SCHEMAS_URI,
)
from material_allocation_copilot.persistence import (
    SqlAlchemyUnitOfWork,
    create_db_engine,
    create_session_factory,
)
from material_allocation_copilot.persistence.models import Base

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


@pytest.fixture
def mcp_context(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'mcp.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('20261011_0006')")
        )
    session_factory = create_session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    material = MaterialItem.model_validate(
        {
            "material_id": "MAT-001",
            "inventory_version_id": "INV-001",
            "source_system": "test_scanner",
            "source_record_id": "SCAN-001",
            "source_file_sha256": HASH_A,
            "source_row_sha256": HASH_B,
            "measured_at": datetime(2026, 10, 6, 9, 0, tzinfo=UTC),
            "length_mm": "4000",
            "section_width_mm": "100",
            "section_height_mm": "200",
            "structural_use_status": StructuralUseStatus.ELIGIBLE,
            "strength_grade": "C24",
            "assessment_method": AssessmentMethod.ENGINEERING_ASSESSMENT,
            "assessor_id": "ENG-01",
            "assessment_date": date(2026, 10, 6),
            "assessment_evidence_ref": "TEST-001",
            "defect_data_status": DefectDataStatus.COMPLETE,
            "defect_evidence_ref": "SCAN-001",
            "defects": [],
            "availability_status": AvailabilityStatus.AVAILABLE,
            "notes": None,
        }
    )
    slot = SlotRequirement.model_validate(
        {
            "project_id": "PRJ-001",
            "slot_requirement_version_id": "SLOTS-v1",
            "slot_id": "SLOT-B01",
            "slot_role": "beam",
            "clear_span_mm": "3000",
            "end_allowance_mm": "100",
            "min_section_width_mm": "90",
            "min_section_height_mm": "190",
            "allow_section_rotation": False,
            "minimum_strength_grade": "C24",
            "orientation_requirement": OrientationRequirement.FIXED,
            "rule_set_version": "rules-0.1.1",
            "requirement_source_ref": "DWG-S201#B01",
            "confirmed_by": "ENG-01",
            "confirmed_at": datetime(2026, 10, 6, 9, 30, tzinfo=UTC),
            "source_row_sha256": HASH_C,
        }
    )
    ProjectService(uow_factory).create_project(project_id="PRJ-001", name="Pilot frame")
    InventoryService(uow_factory).import_inventory(
        InventoryImportInput(
            project_id="PRJ-001",
            inventory_version_id="INV-001",
            idempotency_key="import-001",
            source_system="test_scanner",
            source_path="scanner/batch-001.csv",
            source_sha256=HASH_A,
            imported_at=datetime(2026, 10, 6, 9, 5, tzinfo=UTC),
            materials=(material,),
        )
    )
    RequirementService(uow_factory).save_confirmed(
        ConfirmedRequirementInput(
            requirement_id="REQ-001",
            project_id="PRJ-001",
            idempotency_key="requirement-001",
            slots=(slot,),
        )
    )
    TaskService(uow_factory).create_task(
        allocation_task_id="TASK-001",
        project_id="PRJ-001",
        inventory_version_id="INV-001",
        requirement_id="REQ-001",
        idempotency_key="task-001",
    )
    planner = Principal(
        user_id="PLANNER-01",
        roles=frozenset({Role.PLANNER}),
        project_ids=frozenset({"PRJ-001"}),
    )
    reader = Principal(
        user_id="READER-01",
        roles=frozenset({Role.READER}),
        project_ids=frozenset({"PRJ-001"}),
    )
    yield uow_factory, planner, reader, engine
    engine.dispose()


def test_mcp_catalog_uses_application_registry_and_read_only_annotations(mcp_context) -> None:
    uow_factory, planner, _, _ = mcp_context
    server = create_mcp_server(uow_factory, MCPBinding(planner, "PRJ-001"), run_id="MCP-RUN-001")

    async def scenario() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            expected = {item.name: item for item in default_tool_registry().list()}
            assert {item.name for item in listed.tools} == set(expected)
            for item in listed.tools:
                assert item.input_schema == expected[item.name].input_model.model_json_schema()
                assert item.annotations is not None
                assert item.annotations.read_only_hint is True
                assert item.annotations.destructive_hint is False

            resources = await client.list_resources()
            assert {str(item.uri) for item in resources.resources} == {
                RULE_CATALOG_URI,
                TOOL_SCHEMAS_URI,
            }
            templates = await client.list_resource_templates()
            assert {item.uri_template for item in templates.resource_templates} == {
                PROJECT_SUMMARY_TEMPLATE,
                RUN_SUMMARY_TEMPLATE,
            }
            rules = await client.read_resource(RULE_CATALOG_URI)
            rule_payload = json.loads(rules.contents[0].text)
            assert rule_payload["rule_set_version"] == "rules-0.1.1"
            assert len(rule_payload["rules"]) == 6

    asyncio.run(scenario())


def test_mcp_tool_matches_application_result_and_exposes_audit_resource(mcp_context) -> None:
    uow_factory, planner, _, _ = mcp_context
    server = create_mcp_server(uow_factory, MCPBinding(planner, "PRJ-001"), run_id="MCP-RUN-001")

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("search_inventory", {"inventory_version_id": "INV-001"})
            assert result.is_error is False
            assert result.structured_content is not None
            output = result.structured_content["output"]
            expected = QueryService(uow_factory).search_inventory(
                planner, "PRJ-001", "INV-001", limit=20, offset=0
            )
            assert output["total"] == expected.total
            assert [item["material_id"] for item in output["items"]] == [
                item.material_id for item in expected.items
            ]

            run = await client.read_resource(
                "material-allocation://projects/PRJ-001/runs/MCP-RUN-001"
            )
            run_payload = json.loads(run.contents[0].text)
            assert run_payload["run_id"] == "MCP-RUN-001"
            assert len(run_payload["tool_calls"]) == 1
            assert run_payload["tool_calls"][0]["tool_name"] == "search_inventory"
            assert run_payload["tool_calls"][0]["caller_user_id"] == "PLANNER-01"

    asyncio.run(scenario())


def test_mcp_preserves_role_and_project_boundaries(mcp_context) -> None:
    uow_factory, _, reader, _ = mcp_context
    server = create_mcp_server(uow_factory, MCPBinding(reader, "PRJ-001"), run_id="MCP-RUN-READER")

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("solve_allocation", {"allocation_task_id": "TASK-001"})
            assert result.is_error is True
            assert result.structured_content is not None
            assert result.structured_content["error"]["code"] == "tool_not_allowed"

            summary = await client.read_resource("material-allocation://projects/PRJ-001/summary")
            payload = json.loads(summary.contents[0].text)
            assert payload["project"]["project_id"] == "PRJ-001"
            assert payload["access"]["mode"] == "read_only"

            with pytest.raises(MCPError) as captured:
                await client.read_resource("material-allocation://projects/PRJ-999/summary")
            assert captured.value.code == -32602
            assert captured.value.data == {"application_error_code": "forbidden"}

    asyncio.run(scenario())


def test_real_stdio_client_enforces_tool_allowlist_and_audits_client(mcp_context) -> None:
    _, planner, _, engine = mcp_context
    token = "mcp-local-test-token"
    entry = CredentialEntry(
        token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        principal=planner,
    )
    child_env = os.environ.copy()
    for name in (
        "MAC_OPENAI_API_KEY",
        "MAC_REQUIREMENT_MODEL",
        "MAC_AGENT_MODEL",
        "MAC_AGENT_INPUT_COST_PER_MILLION_USD",
        "MAC_AGENT_OUTPUT_COST_PER_MILLION_USD",
    ):
        child_env.pop(name, None)
    child_env.update(
        {
            "MAC_ENVIRONMENT": "development",
            "MAC_DEVELOPMENT_DATABASE_URL": str(engine.url),
            "MAC_API_PRINCIPALS_JSON": json.dumps([entry.model_dump(mode="json")]),
            "MAC_MCP_BEARER_TOKEN": token,
            "MAC_MCP_PROJECT_ID": "PRJ-001",
            "MAC_MCP_ALLOWED_TOOLS": json.dumps(["search_inventory"]),
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "material_allocation_copilot.mcp_server.server"],
        cwd=Path(__file__).resolve().parents[1],
        env=child_env,
    )

    async def scenario() -> None:
        async with Client(
            parameters,
            client_info=Implementation(name="mcp-test-client", version="1.0.0"),
        ) as client:
            tools = await client.list_tools()
            assert [item.name for item in tools.tools] == ["search_inventory"]

            first = await client.call_tool("search_inventory", {"inventory_version_id": "INV-001"})
            repeated = await client.call_tool(
                "search_inventory", {"inventory_version_id": "INV-001"}
            )
            denied = await client.call_tool(
                "get_material_details",
                {"inventory_version_id": "INV-001", "material_id": "MAT-001"},
            )
            assert first.is_error is False
            assert repeated.is_error is False
            assert (
                first.structured_content["tool_call_id"]
                != repeated.structured_content["tool_call_id"]
            )
            assert denied.is_error is True
            assert denied.structured_content["error"]["code"] == "tool_not_allowed"

            run_id = first.structured_content["run_id"]
            audit = await client.read_resource(
                f"material-allocation://projects/PRJ-001/runs/{run_id}"
            )
            payload = json.loads(audit.contents[0].text)
            assert len(payload["tool_calls"]) == 3
            context = payload["tool_calls"][0]["result_summary"]["invocation_context"]
            assert context == {
                "approval_policy": "operator_tool_allowlist",
                "approval_status": "preapproved_read_only",
                "client_name": "mcp-test-client",
                "client_version": "1.0.0",
                "protocol": "mcp",
                "transport": "stdio_or_in_memory",
            }

            with pytest.raises(MCPError):
                await client.read_resource(
                    "material-allocation://projects/PRJ-001/summary?override=PRJ-999"
                )

    asyncio.run(scenario())
