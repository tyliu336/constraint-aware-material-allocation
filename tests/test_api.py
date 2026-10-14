from __future__ import annotations

import csv
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from material_allocation_copilot.api.app import create_app
from material_allocation_copilot.api.auth import CredentialEntry, CredentialStore
from material_allocation_copilot.api.middleware import MAX_BODY_BYTES
from material_allocation_copilot.application.access import Principal
from material_allocation_copilot.config import AppConfig, DeploymentEnvironment
from material_allocation_copilot.errors import ApplicationError, ErrorCode

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TREETRACE_TABLES = ROOT / ".local-data" / "TreeTrace" / "tables"
LEGACY_TREETRACE_TABLES = Path("D:/timber-match/data/raw/TreeTrace/tables")
REAL_TREETRACE_TABLES = (
    LOCAL_TREETRACE_TABLES if LOCAL_TREETRACE_TABLES.exists() else LEGACY_TREETRACE_TABLES
)
REAL_TREETRACE_BOARDS = REAL_TREETRACE_TABLES / "boards.txt"
BASE = "/api/v1/projects/PRJ-001"
TIME = datetime(2026, 10, 5, 10, tzinfo=UTC).isoformat()
HASH = "a" * 64
TOKENS = {
    "planner": "test-planner-credential",
    "reviewer": "test-reviewer-credential",
    "reader": "test-reader-credential",
    "other": "test-other-project-credential",
}


def headers(role: str, key: str | None = None) -> dict[str, str]:
    values = {"Authorization": f"Bearer {TOKENS[role]}"}
    if key is not None:
        values["Idempotency-Key"] = key
    return values


@pytest.fixture
def api(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    monkeypatch.setenv("MAC_ENVIRONMENT", "test")
    monkeypatch.setenv("MAC_TEST_DATABASE_URL", database_url)
    migration = Config(str(ROOT / "alembic.ini"))
    command.upgrade(migration, "head")
    entries = tuple(
        CredentialEntry(
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            principal=Principal(
                user_id=f"USER-{role}",
                roles=frozenset({"reader" if role == "other" else role}),
                project_ids=frozenset(
                    {"PRJ-OTHER"} if role == "other" else {"PRJ-001", "PRJ-MISSING"}
                ),
            ),
        )
        for role, token in TOKENS.items()
    )
    app = create_app(
        AppConfig(
            environment=DeploymentEnvironment.TEST,
            database_url=database_url,
            treetrace_source_root=REAL_TREETRACE_TABLES,
        ),
        credentials=CredentialStore(entries),
    )
    with TestClient(app) as client:
        yield client, app


def project(client: TestClient):
    response = client.post(
        "/api/v1/projects",
        headers=headers("planner"),
        json={"project_id": "PRJ-001", "name": "API frame"},
    )
    assert response.status_code == 201, response.text
    return response


def test_authenticated_caller_can_read_own_identity(api) -> None:
    client, _ = api

    response = client.get("/api/v1/me", headers=headers("planner"))

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "USER-planner",
        "roles": ["planner"],
        "project_ids": ["PRJ-001", "PRJ-MISSING"],
    }


def material(eligible: bool = False) -> dict[str, object]:
    values: dict[str, object] = {
        "material_id": "MAT-001",
        "inventory_version_id": "INV-001",
        "source_system": "fixture",
        "source_record_id": "ROW-001",
        "source_file_sha256": HASH,
        "source_row_sha256": HASH,
        "length_mm": "4000",
        "section_width_mm": "100",
        "section_height_mm": "200",
    }
    if eligible:
        values.update(
            structural_use_status="eligible",
            strength_grade="C24",
            assessment_method="certified_visual_grading",
            assessor_id="GRADER-001",
            assessment_date="2026-10-04",
            assessment_evidence_ref="REPORT-001",
            defect_data_status="complete",
            availability_status="available",
        )
    return values


def slot() -> dict[str, object]:
    return {
        "project_id": "PRJ-001",
        "slot_requirement_version_id": "SLOTS-v1",
        "slot_id": "SLOT-001",
        "slot_role": "beam",
        "clear_span_mm": "3000",
        "end_allowance_mm": "100",
        "min_section_width_mm": "90",
        "min_section_height_mm": "190",
        "allow_section_rotation": False,
        "minimum_strength_grade": "C24",
        "orientation_requirement": "fixed",
        "rule_set_version": "rules-0.1.1",
        "requirement_source_ref": "DWG-001",
        "confirmed_by": "USER-reviewer",
        "confirmed_at": TIME,
        "source_row_sha256": HASH,
    }


def inventory_body(eligible: bool = False):
    return {
        "inventory_version_id": "INV-001",
        "source_system": "fixture",
        "source_path": "fixture.csv",
        "source_sha256": HASH,
        "imported_at": TIME,
        "materials": [material(eligible)],
    }


def inputs(client: TestClient, *, eligible: bool = False):
    project(client)
    response = client.post(
        BASE + "/inventory/import",
        headers=headers("planner", "import-001"),
        json=inventory_body(eligible),
    )
    assert response.status_code == 201, response.text
    response = client.post(
        BASE + "/requirements/confirm",
        headers=headers("reviewer", "req-001"),
        json={"requirement_id": "REQ-001", "slots": [slot()]},
    )
    assert response.status_code == 201, response.text
    response = client.post(
        BASE + "/tasks",
        headers=headers("planner", "task-001"),
        json={
            "allocation_task_id": "TASK-001",
            "inventory_version_id": "INV-001",
            "requirement_id": "REQ-001",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["input_snapshot_hash"]


def test_agent_route_is_fail_closed_without_configured_model(api):
    client, _ = api
    inputs(client, eligible=True)
    response = client.post(
        BASE + "/agent-runs",
        headers=headers("planner"),
        json={
            "run_id": "RUN-001",
            "allocation_task_id": "TASK-001",
            "goal": "Find a feasible allocation preview.",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "model_unavailable"


def plan_body(snapshot_hash: str, *, status: str = "requires_review"):
    return {
        "allocation_plan_id": "PLAN-001",
        "plan_version": 1,
        "terminal_status": status,
        "input_snapshot_hash": snapshot_hash,
        "assignments": [{"slot_id": "SLOT-001", "material_id": "MAT-001"}],
    }


@pytest.mark.parametrize("eligible", [False, True])
def test_api_workflow_preserves_task_plan_rules_approval_and_export_states(api, eligible: bool):
    client, _ = api
    snapshot = inputs(client, eligible=eligible)
    task = client.get(BASE + "/tasks/TASK-001", headers=headers("reader"))
    assert task.json()["status"] == "pending"
    pair = client.post(
        BASE + "/tasks/TASK-001/validate-pair",
        headers=headers("reader"),
        json={"material_id": "MAT-001", "slot_id": "SLOT-001"},
    )
    assert pair.status_code == 200, pair.text
    assert len(pair.json()["rule_evaluations"]) == 5
    status = "complete_feasible" if eligible else "requires_review"
    response = client.post(
        BASE + "/tasks/TASK-001/plans",
        headers=headers("planner"),
        json=plan_body(snapshot, status=status),
    )
    assert response.status_code == 201, response.text
    assert len(response.json()["rule_evaluations"]) == 6
    assert response.json()["terminal_status"] == status
    assert response.json()["approval"] is None
    assert response.json()["execution_authorized"] is False
    history = client.get(
        BASE + "/tasks/TASK-001/plans?limit=50&offset=0", headers=headers("reader")
    )
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["allocation_plan_id"] == "PLAN-001"
    assert history.json()["items"][0]["plan_version"] == 1
    decision = {"approval_id": "APR-001", "decision": "approved", "note": "Reviewed."}
    approval = client.post(
        BASE + "/plans/PLAN-001/approval", headers=headers("reviewer", "approve-001"), json=decision
    )
    assert approval.status_code == 201, approval.text
    retry = client.post(
        BASE + "/plans/PLAN-001/approval", headers=headers("reviewer", "approve-001"), json=decision
    )
    assert retry.json() == approval.json()
    assert approval.json()["decided_by"] == "USER-reviewer"
    fetched = client.get(BASE + "/plans/PLAN-001", headers=headers("reader"))
    assert fetched.json()["terminal_status"] == status
    assert fetched.json()["approval"]["decision"] == "approved"
    assert fetched.json()["execution_authorized"] is False
    reservation = client.post(
        BASE + "/plans/PLAN-001/reservation",
        headers=headers("reviewer", "reserve-001"),
        json={"reservation_id": "RSV-001"},
    )
    assert reservation.status_code == 201, reservation.text
    assert reservation.json()["status"] == "active"
    assert reservation.json()["material_ids"] == ["MAT-001"]
    assert (
        client.post(
            BASE + "/plans/PLAN-001/reservation",
            headers=headers("reviewer", "reserve-001"),
            json={"reservation_id": "RSV-001"},
        ).json()
        == reservation.json()
    )
    assert (
        client.get(BASE + "/reservations/RSV-001", headers=headers("reader")).json()
        == reservation.json()
    )
    released = client.post(
        BASE + "/reservations/RSV-001/release",
        headers=headers("reviewer", "release-001"),
    )
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "released"
    assert (
        client.get(BASE + "/tasks/TASK-001", headers=headers("reader")).json()["status"]
        == "completed"
    )
    evidence = client.get(
        BASE + "/plans/PLAN-001/evaluations?limit=2&offset=1", headers=headers("reader")
    )
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["total"] == 6
    assert len(evidence.json()["items"]) == 2
    exported = client.get(BASE + "/plans/PLAN-001/export?format=csv", headers=headers("reader"))
    assert exported.status_code == 200
    assert exported.headers["x-export-purpose"] == "review"
    row = next(csv.DictReader(StringIO(exported.text)))
    assert row["execution_authorized"] == "false"
    assert row["approval_decision"] == "approved"


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_authentication_is_required_and_correlated(api, token):
    client, _ = api
    request_headers = {"X-Request-ID": "trace-test-001"}
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    response = client.get("/api/v1/projects", headers=request_headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["x-request-id"] == response.json()["request_id"] == "trace-test-001"
    assert response.json()["error"]["code"] == "unauthenticated"


def test_liveness_readiness_and_low_cardinality_metrics(api):
    client, _ = api
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {
        "status": "ready",
        "database": "ready",
    }
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["requests_total"] >= 2
    assert metrics.json()["duration_ms_max"] >= 0


def test_task_cancel_and_retry_api_is_idempotent_and_bounded(api):
    client, _ = api
    inputs(client, eligible=True)
    cancelled = client.post(
        BASE + "/tasks/TASK-001/cancel",
        headers=headers("planner"),
        json={"reason": "Inputs changed"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    retried = client.post(
        BASE + "/tasks/TASK-001/retry",
        headers=headers("planner", "retry-002"),
        json={"allocation_task_id": "TASK-002"},
    )
    assert retried.status_code == 201, retried.text
    assert retried.json()["attempt"] == 2
    assert retried.json()["retry_of_task_id"] == "TASK-001"
    duplicate = client.post(
        BASE + "/tasks/TASK-001/retry",
        headers=headers("planner", "retry-002"),
        json={"allocation_task_id": "TASK-002"},
    )
    assert duplicate.json() == retried.json()


def test_reader_cannot_write_and_other_project_cannot_read(api):
    client, _ = api
    project(client)
    denied = client.post(
        "/api/v1/projects",
        headers=headers("reader"),
        json={"project_id": "PRJ-001", "name": "Changed"},
    )
    assert denied.status_code == 403
    assert client.get(BASE, headers=headers("other")).status_code == 403
    assert client.get("/api/v1/projects", headers=headers("other")).json()["total"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "/inventory/INV-001",
        "/requirements/REQ-001",
        "/tasks/TASK-001",
        "/tasks/TASK-001/candidates",
        "/plans/PLAN-001",
        "/plans/PLAN-001/evaluations",
        "/plans/PLAN-001/export",
    ],
)
def test_resource_lookup_does_not_cross_project_scopes(api, path):
    client, _ = api
    snapshot = inputs(client)
    assert (
        client.post(
            BASE + "/tasks/TASK-001/plans", headers=headers("planner"), json=plan_body(snapshot)
        ).status_code
        == 201
    )
    # The caller is permitted to this project, but these resource IDs belong to a different project.
    assert (
        client.post(
            "/api/v1/projects",
            headers=headers("planner"),
            json={"project_id": "PRJ-MISSING", "name": "Second project"},
        ).status_code
        == 201
    )
    response = client.get("/api/v1/projects/PRJ-MISSING" + path, headers=headers("reader"))
    assert response.status_code == 404, response.text


def test_invalid_input_conflict_not_found_and_internal_error_have_stable_envelopes(
    api, monkeypatch
):
    client, app = api
    bad = client.post(
        "/api/v1/projects", headers=headers("planner"), json={"project_id": "", "name": "X"}
    )
    assert bad.status_code == 422
    assert "input" not in str(bad.json()["error"]["context"].get("errors"))
    project(client)
    conflict = client.post(
        "/api/v1/projects",
        headers=headers("planner"),
        json={"project_id": "PRJ-001", "name": "Changed"},
    )
    assert conflict.status_code == 409
    missing = client.get("/api/v1/projects/PRJ-MISSING", headers=headers("reader"))
    assert missing.status_code == 404

    def fail(*args, **kwargs):
        raise RuntimeError("private SQL, credential and filesystem details")

    monkeypatch.setattr(app.state.workflow.queries, "get_project", fail)
    internal = client.get(BASE, headers={**headers("reader"), "X-Request-ID": "internal-001"})
    assert internal.status_code == 500
    assert internal.json()["error"]["code"] == "internal_error"
    assert internal.headers["x-request-id"] == internal.json()["request_id"] == "internal-001"
    assert "private" not in internal.text


def test_cross_field_validation_is_422_instead_of_500(api):
    client, _ = api
    project(client)
    body = inventory_body()
    body["source_sha256"] = "b" * 64
    response = client.post(
        BASE + "/inventory/import", headers=headers("planner", "import-bad"), json=body
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (50, -1)])
def test_pagination_limits_are_validated(api, limit, offset):
    client, _ = api
    response = client.get(
        f"/api/v1/projects?limit={limit}&offset={offset}", headers=headers("reader")
    )
    assert response.status_code == 422


def test_request_ids_are_returned_on_success_and_unsafe_values_are_replaced(api):
    client, _ = api
    response = client.get("/health", headers={"X-Request-ID": "normal-001"})
    assert response.headers["x-request-id"] == "normal-001"
    response = client.get("/health", headers={"X-Request-ID": "unsafe spaces"})
    assert len(response.headers["x-request-id"]) == 32


def test_oversized_and_chunked_bodies_are_rejected(api):
    client, _ = api
    response = client.post(
        BASE + "/inventory/csv?inventory_version_id=INV-001",
        headers={**headers("planner", "large-import"), "Content-Type": "text/csv"},
        content=b"x" * (MAX_BODY_BYTES + 1),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    response = client.post(
        BASE + "/inventory/csv?inventory_version_id=INV-001",
        headers={**headers("planner", "large-import"), "Content-Type": "text/csv"},
        content=iter([b"x" * MAX_BODY_BYTES, b"x"]),
    )
    assert response.status_code == 413


def test_openapi_exposes_auth_contracts_and_does_not_expose_orm(api):
    client, _ = api
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    operation = schema["paths"][BASE.replace("PRJ-001", "{project_id}") + "/tasks"]["post"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert "422" in operation["responses"] and "409" in operation["responses"]
    assert "PlanResponse" in schema["components"]["schemas"]
    assert all(not key.endswith("Row") for key in schema["components"]["schemas"])


def csv_bytes(row: dict[str, object]) -> bytes:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def test_csv_inventory_and_slots_provenance_retries_and_pagination(api):
    client, _ = api
    project(client)
    row = material(eligible=True)
    for field in (
        "inventory_version_id",
        "source_system",
        "source_file_sha256",
        "source_row_sha256",
    ):
        del row[field]
    raw = csv_bytes(row)
    endpoint = BASE + "/inventory/csv?inventory_version_id=INV-001"
    request_headers = {**headers("planner", "csv-import-001"), "Content-Type": "text/csv"}
    imported = client.post(endpoint, headers=request_headers, content=raw)
    assert imported.status_code == 201, imported.text
    retry = client.post(endpoint, headers=request_headers, content=raw)
    assert retry.json() == imported.json()
    assert imported.json()["source_sha256"] == hashlib.sha256(raw).hexdigest()
    materials = client.get(BASE + "/inventory/INV-001/materials?limit=1", headers=headers("reader"))
    assert materials.json()["total"] == 1
    item = materials.json()["items"][0]
    assert item["source_system"] == "normalized_csv"
    assert item["inventory_version_id"] == "INV-001"
    assert item["source_file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(item["source_row_sha256"]) == 64
    row["length_mm"] = "5000"
    assert client.post(endpoint, headers=request_headers, content=csv_bytes(row)).status_code == 409
    assert (
        client.get(BASE + "/inventory/INV-001/materials", headers=headers("reader")).json()[
            "items"
        ][0]["length_mm"]
        == "4000"
    )

    slot_row = slot()
    for field in ("project_id", "slot_requirement_version_id", "confirmed_by", "source_row_sha256"):
        del slot_row[field]
    endpoint = BASE + "/slots/import?requirement_id=REQ-001&slot_requirement_version_id=SLOTS-v1"
    request_headers = {**headers("reviewer", "csv-slots-001"), "Content-Type": "text/csv"}
    raw = csv_bytes(slot_row)
    confirmed = client.post(endpoint, headers=request_headers, content=raw)
    assert confirmed.status_code == 201, confirmed.text
    assert client.post(endpoint, headers=request_headers, content=raw).json() == confirmed.json()
    assert confirmed.json()["confirmed_by"] == "USER-reviewer"
    slots = client.get(BASE + "/requirements/REQ-001/slots?offset=1", headers=headers("reader"))
    assert slots.json()["total"] == 1 and slots.json()["items"] == []


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"material_id,material_id\na,b\n",
        b"material_id\na,b\n",
        b"material_id,source_system\nMAT-001,spoofed\n",
        b"\xff",
    ],
)
def test_csv_bad_structure_and_controlled_fields_are_422(api, raw):
    client, _ = api
    project(client)
    response = client.post(
        BASE + "/inventory/csv?inventory_version_id=INV-001",
        headers={**headers("planner", "invalid-csv"), "Content-Type": "text/csv"},
        content=raw,
    )
    assert response.status_code == 422, response.text


def test_confirmation_and_approval_cannot_spoof_authenticated_identity(api):
    client, _ = api
    snapshot = inputs(client)
    changed_slot = slot()
    changed_slot["confirmed_by"] = "USER-someone-else"
    request = {"requirement_id": "REQ-002", "slots": [changed_slot]}
    assert (
        client.post(
            BASE + "/requirements/confirm", headers=headers("reviewer", "spoof"), json=request
        ).status_code
        == 403
    )
    assert (
        client.post(
            BASE + "/requirements/confirm", headers=headers("planner", "spoof"), json=request
        ).status_code
        == 403
    )
    client.post(
        BASE + "/tasks/TASK-001/plans", headers=headers("planner"), json=plan_body(snapshot)
    )
    decision = {"approval_id": "APR-001", "decision": "approved", "decided_by": "USER-admin"}
    assert (
        client.post(
            BASE + "/plans/PLAN-001/approval", headers=headers("reviewer", "spoof"), json=decision
        ).status_code
        == 422
    )


def test_api_rejects_stale_snapshot_and_false_feasibility_without_completing_task(api):
    client, _ = api
    snapshot = inputs(client)
    path = BASE + "/tasks/TASK-001/plans"
    assert (
        client.post(path, headers=headers("planner"), json=plan_body("b" * 64)).status_code == 409
    )
    assert (
        client.post(
            path, headers=headers("planner"), json=plan_body(snapshot, status="complete_feasible")
        ).status_code
        == 422
    )
    assert (
        client.get(BASE + "/tasks/TASK-001", headers=headers("reader")).json()["status"]
        == "pending"
    )
    assert client.get(BASE + "/plans/PLAN-001", headers=headers("reader")).status_code == 404


def test_api_treetrace_import_uses_read_only_source_and_preserves_missing_evidence(api):
    source = REAL_TREETRACE_BOARDS
    if not source.is_file():
        pytest.skip("Real TreeTrace fixture is unavailable")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    client, _ = api
    project(client)
    endpoint = BASE + "/inventory/treetrace"
    kwargs = {
        "headers": headers("planner", "treetrace-api"),
        "json": {"inventory_version_id": "INV-TREETRACE"},
    }
    imported = client.post(endpoint, **kwargs)
    assert imported.status_code == 201, imported.text
    assert imported.json()["item_count"] == 346
    assert client.post(endpoint, **kwargs).json() == imported.json()
    page = client.get(
        BASE + "/inventory/INV-TREETRACE/materials?limit=2", headers=headers("reader")
    )
    assert page.json()["total"] == 346
    assert all(item["structural_use_status"] == "not_evaluable" for item in page.json()["items"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


def test_startup_refuses_unmigrated_database_without_creating_product_tables(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'unmigrated.db').as_posix()}"
    app = create_app(
        AppConfig(environment=DeploymentEnvironment.TEST, database_url=database_url),
        credentials=CredentialStore(()),
    )
    with pytest.raises(ApplicationError) as caught, TestClient(app):
        pass
    assert caught.value.code == ErrorCode.CONFIGURATION_ERROR
    engine = create_engine(database_url)
    try:
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_empty_credentials_are_fail_closed_and_invalid_config_is_redacted(api, monkeypatch):
    client, app = api
    app.state.credentials = CredentialStore(())
    assert client.get("/api/v1/projects", headers=headers("planner")).status_code == 401
    monkeypatch.setenv("MAC_API_PRINCIPALS_JSON", "secret-invalid-configuration")
    with pytest.raises(ApplicationError) as caught:
        CredentialStore.from_env()
    assert "secret-invalid" not in str(caught.value)


@pytest.mark.parametrize("eligible", [True, False])
def test_solver_preview_is_rechecked_read_only_and_can_be_saved_separately(api, eligible):
    client, _ = api
    snapshot_hash = inputs(client, eligible=eligible)
    response = client.post(BASE + "/tasks/TASK-001/solve", headers=headers("planner"))
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["input_snapshot_hash"] == snapshot_hash
    assert preview["preview_only"] and preview["execution_authorized"] is False
    assert preview["terminal_status"] == ("complete_feasible" if eligible else "requires_review")
    assert preview["assignment_basis"] == ("verified" if eligible else "optimistic")
    assert len(preview["assignments"]) == 1
    assert len(preview["rule_evaluations"]) == 6
    assert len(preview["candidate_eligibility_checks"]) == 2
    assert preview["analysis"]["verified_matching"]["maximum_cardinality_proven"]
    exact = preview["analysis"]["verified_matching"]
    assert exact["minimum_vertex_cover"]["size"] == exact["matched_count"]
    assert preview["analysis"]["optimistic_matching"]["graph_scope"] == "optimistic"
    assert client.post(BASE + "/tasks/TASK-001/solve", headers=headers("planner")).json() == preview
    assert (
        client.get(BASE + "/tasks/TASK-001", headers=headers("reader")).json()["status"]
        == "pending"
    )
    assert client.get(BASE + "/plans/PLAN-001", headers=headers("reader")).status_code == 404
    saved = client.post(
        BASE + "/tasks/TASK-001/plans",
        headers=headers("planner"),
        json={
            "allocation_plan_id": "PLAN-001",
            "plan_version": 1,
            **{
                field: preview[field]
                for field in (
                    "terminal_status",
                    "input_snapshot_hash",
                    "assignments",
                    "rule_evaluations",
                )
            },
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["terminal_status"] == preview["terminal_status"]
    assert saved.json()["approval"] is None
    assert saved.json()["execution_authorized"] is False


def task_with_rows(client, materials, slots):
    project(client)
    body = inventory_body(eligible=True)
    body["materials"] = materials
    response = client.post(
        BASE + "/inventory/import", headers=headers("planner", "matrix-import"), json=body
    )
    assert response.status_code == 201, response.text
    response = client.post(
        BASE + "/requirements/confirm",
        headers=headers("reviewer", "matrix-req"),
        json={"requirement_id": "REQ-001", "slots": slots},
    )
    assert response.status_code == 201, response.text
    response = client.post(
        BASE + "/tasks",
        headers=headers("planner", "matrix-task"),
        json={
            "allocation_task_id": "TASK-001",
            "inventory_version_id": "INV-001",
            "requirement_id": "REQ-001",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["input_snapshot_hash"]


def test_api_matrix_pagination_exclusions_and_exact_vs_greedy(api):
    client, _ = api
    long = {**material(True), "length_mm": "5000"}
    short = {**material(True), "material_id": "MAT-002", "length_mm": "3200"}
    second_slot = {**slot(), "slot_id": "SLOT-002", "clear_span_mm": "4500"}
    task_with_rows(client, [long, short], [slot(), second_slot])
    pairs = client.get(
        BASE + "/tasks/TASK-001/candidates?limit=2&offset=1", headers=headers("reader")
    )
    assert pairs.status_code == 200, pairs.text
    page = pairs.json()
    assert page["total"] == page["summary"]["pair_count"] == 4
    assert page["summary"]["pass_count"] == 3 and page["summary"]["fail_count"] == 1
    assert len(page["items"]) == 2
    failed = client.get(BASE + "/tasks/TASK-001/candidates?outcome=fail", headers=headers("reader"))
    assert failed.json()["total"] == 1
    pair = failed.json()["items"][0]
    assert (pair["slot_id"], pair["material_id"]) == ("SLOT-002", "MAT-002")
    assert pair["rule_results"][0]["outcome"] == "fail"
    assert pair["rule_results"][0]["evidence"]
    solved = client.post(BASE + "/tasks/TASK-001/solve", headers=headers("planner"))
    assert solved.status_code == 200, solved.text
    result = solved.json()
    assert result["analysis"]["summary"]["matrix_hash"] == page["summary"]["matrix_hash"]
    assert result["terminal_status"] == "complete_feasible"
    assert result["analysis"]["greedy_baseline"]["status"] == "method_no_complete_solution"
    assert result["analysis"]["verified_matching"]["status"] == "complete"
    assert result["analysis"]["verified_matching"]["matched_count"] == 2
    assert [item["material_id"] for item in result["assignments"]] == ["MAT-002", "MAT-001"]


def test_api_infeasibility_has_optimistic_graph_witness_not_only_greedy_failure(api):
    client, _ = api
    task_with_rows(client, [material(False)], [slot(), {**slot(), "slot_id": "SLOT-002"}])
    response = client.post(BASE + "/tasks/TASK-001/solve", headers=headers("planner"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["terminal_status"] == "infeasible" and result["assignments"] == []
    witness = result["analysis"]["optimistic_matching"]["hall_witness"]
    assert witness == {
        "slot_ids": ["SLOT-001", "SLOT-002"],
        "neighbor_material_ids": ["MAT-001"],
        "shortage": 1,
    }
    assert result["analysis"]["reason"] == "known_constraints_prevent_complete_matching"
    assert (
        client.get(BASE + "/tasks/TASK-001", headers=headers("reader")).json()["status"]
        == "pending"
    )


def test_api_unsupported_constraint_stays_reviewable_even_if_existing_rules_pass(api):
    client, _ = api
    task_with_rows(client, [material(True)], [{**slot(), "max_moisture_content_pct": "20"}])
    response = client.post(BASE + "/tasks/TASK-001/solve", headers=headers("planner"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["terminal_status"] == "requires_review"
    assert all(item["result"]["outcome"] == "pass" for item in result["rule_evaluations"])
    coverage = result["candidate_eligibility_checks"][1]["result"]
    assert coverage["outcome"] == "not_evaluable"
    assert coverage["evidence"][0]["field_name"] == "max_moisture_content_pct"


def test_candidate_and_solve_permissions_project_scope_invalid_filter_and_limits(api, monkeypatch):
    client, _ = api
    inputs(client)
    path = BASE + "/tasks/TASK-001"
    assert client.post(path + "/solve", headers=headers("reader")).status_code == 403
    assert client.post(path + "/solve", headers=headers("other")).status_code == 403
    assert client.get(path + "/candidates", headers=headers("other")).status_code == 403
    assert (
        client.get(path + "/candidates?outcome=unknown", headers=headers("reader")).status_code
        == 422
    )
    assert client.get(path + "/candidates?limit=101", headers=headers("reader")).status_code == 422
    assert (
        client.get(path + "/candidates?offset=100", headers=headers("reader")).json()["items"] == []
    )
    assert (
        client.post(
            "/api/v1/projects/PRJ-MISSING/tasks/TASK-001/solve", headers=headers("planner")
        ).status_code
        == 404
    )
    monkeypatch.setattr("material_allocation_copilot.solver.candidates.MAX_CANDIDATE_PAIRS", 0)
    for method, suffix in ((client.get, "/candidates"), (client.post, "/solve")):
        response = method(path + suffix, headers=headers("planner"))
        assert response.status_code == 422
        assert response.json()["error"]["context"]["maximum_pair_count"] == 0
    assert client.get(path, headers=headers("reader")).json()["status"] == "pending"


def test_solver_and_candidate_endpoints_are_documented(api):
    client, _ = api
    schema = client.get("/openapi.json").json()
    prefix = "/api/v1/projects/{project_id}/tasks/{task_id}"
    assert schema["paths"][prefix + "/solve"]["post"]["security"] == [{"HTTPBearer": []}]
    assert schema["paths"][prefix + "/candidates"]["get"]["security"] == [{"HTTPBearer": []}]
    for name in ("SolvePreview", "CandidatePage", "HallWitness", "MatchingResult"):
        assert name in schema["components"]["schemas"]


def test_solver_claim_cannot_bypass_authoritative_final_recheck(api, monkeypatch):
    import material_allocation_copilot.application.candidates as candidate_application

    client, _ = api
    inputs(client, eligible=False)
    original = candidate_application.check_feasibility

    def claim_false_feasibility(matrix):
        analysis = original(matrix)
        return analysis.model_copy(
            update={
                "assessment": "complete_feasible",
                "verified_matching": analysis.optimistic_matching,
            }
        )

    monkeypatch.setattr(candidate_application, "check_feasibility", claim_false_feasibility)
    response = client.post(BASE + "/tasks/TASK-001/solve", headers=headers("planner"))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "input_invalid"
    assert (
        client.get(BASE + "/tasks/TASK-001", headers=headers("reader")).json()["status"]
        == "pending"
    )


def test_real_treetrace_candidate_matrix_and_solver_do_not_invent_structural_evidence(api):
    source = REAL_TREETRACE_BOARDS
    if not source.is_file():
        pytest.skip("Real TreeTrace fixture is unavailable")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    client, _ = api
    project(client)
    imported = client.post(
        BASE + "/inventory/treetrace",
        headers=headers("planner", "solver-tt"),
        json={"inventory_version_id": "INV-TT"},
    )
    assert imported.status_code == 201, imported.text
    # Artificial geometry avoids size failures. TreeTrace still cannot supply structural approval.
    slots = [
        {
            **slot(),
            "slot_id": f"SLOT-{number:03}",
            "clear_span_mm": "100",
            "end_allowance_mm": "0",
            "min_section_width_mm": "1",
            "min_section_height_mm": "1",
        }
        for number in range(12)
    ]
    confirmed = client.post(
        BASE + "/requirements/confirm",
        headers=headers("reviewer", "solver-req"),
        json={"requirement_id": "REQ-TT", "slots": slots},
    )
    assert confirmed.status_code == 201, confirmed.text
    task = client.post(
        BASE + "/tasks",
        headers=headers("planner", "solver-task"),
        json={
            "allocation_task_id": "TASK-TT",
            "inventory_version_id": "INV-TT",
            "requirement_id": "REQ-TT",
        },
    )
    assert task.status_code == 201, task.text
    solved = client.post(BASE + "/tasks/TASK-TT/solve", headers=headers("planner"))
    assert solved.status_code == 200, solved.text
    result = solved.json()
    assert result["terminal_status"] == "requires_review"
    assert result["assignment_basis"] == "optimistic"
    summary = result["analysis"]["summary"]
    assert summary["pair_count"] == 346 * 12
    assert summary["not_evaluable_count"] == 95 * 12
    assert summary["fail_count"] == 251 * 12
    assert summary["pass_count"] == 0
    assert result["analysis"]["verified_matching"]["assignments"] == []
    assert (
        len(result["assignments"])
        == len({row["material_id"] for row in result["assignments"]})
        == 12
    )
    assert any(item["result"]["outcome"] == "not_evaluable" for item in result["rule_evaluations"])
    assert result["execution_authorized"] is False
    assert result["analysis"]["optimistic_is_hypothetical"] is True
    ranked = client.post(
        BASE + "/tasks/TASK-TT/ranked-plans",
        headers=headers("planner"),
        json={
            "preferences": [
                {"preference_type": "minimize_cut_waste", "weight": "1"},
                {"preference_type": "preserve_scarce_long_stock", "weight": "1"},
                {"preference_type": "minimize_not_evaluable", "weight": "1"},
            ],
            "max_plans": 3,
        },
    )
    assert ranked.status_code == 200, ranked.text
    ranked_result = ranked.json()
    assert ranked_result["terminal_status"] == "requires_review"
    assert len(ranked_result["plans"]) == 3
    assert all(len(plan["assignments"]) == 12 for plan in ranked_result["plans"])
    assert all(plan["execution_authorized"] is False for plan in ranked_result["plans"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


def test_ranked_plans_order_soft_preferences_but_remain_read_only_until_saved(api):
    client, _ = api
    long = {**material(True), "material_id": "MAT-001", "length_mm": "5000"}
    short = {**material(True), "material_id": "MAT-002", "length_mm": "3200"}
    snapshot_hash = task_with_rows(client, [long, short], [slot()])
    response = client.post(
        BASE + "/tasks/TASK-001/ranked-plans",
        headers=headers("planner"),
        json={
            "preferences": [{"preference_type": "minimize_cut_waste", "weight": "7"}],
            "max_plans": 3,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["terminal_status"] == "complete_feasible"
    assert result["graph_scope"] == "verified"
    assert result["search_status"] == "all_feasible_plans_enumerated"
    assert [plan["assignments"][0]["material_id"] for plan in result["plans"]] == [
        "MAT-002",
        "MAT-001",
    ]
    assert result["normalized_preferences"][0]["normalized_weight"] == "1"
    assert result["plans"][0]["assignment_scores"][0]["criteria"][0]["actual_value"] == (
        "100 mm length surplus"
    )
    assert result["baselines"][0]["method"] == "unweighted_exact"
    assert Decimal(result["baselines"][0]["difference_from_ranked_best"]) > 0
    assert result["preview_only"] and result["execution_authorized"] is False
    no_effect = client.post(
        BASE + "/tasks/TASK-001/ranked-plans",
        headers=headers("planner"),
        json={
            "preferences": [{"preference_type": "minimize_not_evaluable", "weight": "1"}],
            "max_plans": 2,
        },
    ).json()
    assert any(item["code"] == "no_ranking_effect" for item in no_effect["diagnostics"])
    assert (
        client.post(
            BASE + "/tasks/TASK-001/ranked-plans",
            headers=headers("planner"),
            json={
                "preferences": [{"preference_type": "minimize_cut_waste", "weight": "7"}],
                "max_plans": 3,
            },
        ).json()
        == result
    )
    assert (
        client.get(BASE + "/tasks/TASK-001", headers=headers("reader")).json()["status"]
        == "pending"
    )
    assert client.get(BASE + "/plans/PLAN-001", headers=headers("reader")).status_code == 404

    best = result["plans"][0]
    saved = client.post(
        BASE + "/tasks/TASK-001/plans",
        headers=headers("planner"),
        json={
            "allocation_plan_id": "PLAN-001",
            "plan_version": 1,
            "terminal_status": result["terminal_status"],
            "input_snapshot_hash": snapshot_hash,
            "assignments": best["assignments"],
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["assignments"] == best["assignments"]
    assert saved.json()["approval"] is None and saved.json()["execution_authorized"] is False


def test_ranked_plans_use_surface_knot_evidence(api):
    client, _ = api
    clean = {
        **material(True),
        "material_id": "MAT-clean",
        "surface_knots": [
            {
                "face": "wide",
                "scope": "whole",
                "knot_type_code": "S0",
                "size_mm": "8",
                "source_ref": "inspection:clean:wide",
            },
            {
                "face": "narrow",
                "scope": "whole",
                "knot_type_code": "0",
                "size_mm": "0",
                "source_ref": "inspection:clean:narrow",
            },
        ],
    }
    severe = {
        **material(True),
        "material_id": "MAT-severe",
        "surface_knots": [
            {
                "face": "wide",
                "scope": "whole",
                "knot_type_code": "G5",
                "size_mm": "100",
                "source_ref": "inspection:severe:wide",
            },
            {
                "face": "narrow",
                "scope": "whole",
                "knot_type_code": "S3",
                "size_mm": "70",
                "source_ref": "inspection:severe:narrow",
            },
        ],
    }
    task_with_rows(client, [severe, clean], [slot()])

    response = client.post(
        BASE + "/tasks/TASK-001/ranked-plans",
        headers=headers("planner"),
        json={
            "preferences": [{"preference_type": "minimize_surface_defects", "weight": "10"}],
            "max_plans": 2,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["scoring_policy_version"] == "soft-scoring-1.1.0"
    assert [plan["assignments"][0]["material_id"] for plan in result["plans"]] == [
        "MAT-clean",
        "MAT-severe",
    ]
    criterion = result["plans"][0]["assignment_scores"][0]["criteria"][0]
    assert criterion["preference_type"] == "minimize_surface_defects"
    assert criterion["status"] == "scored"


def test_ranked_plans_expose_weight_normalization_tradeoffs_and_unknowns(api):
    client, _ = api
    known = {**material(True), "material_id": "MAT-known", "length_mm": "4000"}
    low_unknown = {
        **material(True),
        "material_id": "MAT-low-unknown",
        "length_mm": "6000",
        "structural_use_status": "not_evaluable",
        "assessment_method": "unknown",
        "assessor_id": None,
        "assessment_date": None,
        "assessment_evidence_ref": None,
    }
    high_unknown = {
        **low_unknown,
        "material_id": "MAT-high-unknown",
        "length_mm": "4000",
        "defect_data_status": "unknown",
        "availability_status": "unknown",
    }
    task_with_rows(
        client,
        [known, low_unknown, high_unknown],
        [slot(), {**slot(), "slot_id": "SLOT-002", "clear_span_mm": "3800"}],
    )
    response = client.post(
        BASE + "/tasks/TASK-001/ranked-plans",
        headers=headers("planner"),
        json={
            "preferences": [
                {"preference_type": "minimize_cut_waste", "weight": "1"},
                {"preference_type": "minimize_not_evaluable", "weight": "3"},
            ],
            "max_plans": 5,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["terminal_status"] == "requires_review"
    assert result["graph_scope"] == "optimistic"
    assert result["missing_soft_evidence_policy"] == "explicit_not_evaluable_zero_score_v1"
    weights = {
        item["preference_type"]: item["normalized_weight"]
        for item in result["normalized_preferences"]
    }
    assert weights == {"minimize_cut_waste": "0.25", "minimize_not_evaluable": "0.75"}
    assert any(item["code"] == "objective_tradeoff" for item in result["diagnostics"])
    assert all(plan["execution_authorized"] is False for plan in result["plans"])
    assert any(
        criterion["preference_type"] == "minimize_not_evaluable" and Decimal(criterion["score"]) < 1
        for plan in result["plans"]
        for pair in plan["assignment_scores"]
        for criterion in pair["criteria"]
    )
    waste_first = client.post(
        BASE + "/tasks/TASK-001/ranked-plans",
        headers=headers("planner"),
        json={
            "preferences": [
                {"preference_type": "minimize_cut_waste", "weight": "10"},
                {"preference_type": "minimize_not_evaluable", "weight": "1"},
            ],
            "max_plans": 5,
        },
    ).json()
    assert waste_first["matrix_hash"] == result["matrix_hash"]
    assert waste_first["feasibility"]["summary"] == result["feasibility"]["summary"]
    assert waste_first["plans"][0]["plan_hash"] != result["plans"][0]["plan_hash"]
    assert waste_first["terminal_status"] == result["terminal_status"] == "requires_review"


def test_ranked_plans_return_proven_infeasibility_without_partial_soft_plan(api):
    client, _ = api
    task_with_rows(client, [material(True)], [slot(), {**slot(), "slot_id": "SLOT-002"}])
    response = client.post(
        BASE + "/tasks/TASK-001/ranked-plans",
        headers=headers("planner"),
        json={"preferences": [{"preference_type": "minimize_cut_waste", "weight": "1"}]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["terminal_status"] == result["search_status"] == "infeasible"
    assert result["graph_scope"] == "none"
    assert result["plans"] == [] and result["baselines"] == []
    assert result["feasibility"]["optimistic_matching"]["hall_witness"]["shortage"] == 1


@pytest.mark.parametrize(
    "body",
    [
        {"preferences": [{"preference_type": "minimize_cut_waste", "weight": "0"}]},
        {"preferences": [{"preference_type": "unknown", "weight": "1"}]},
        {
            "preferences": [
                {"preference_type": "minimize_cut_waste", "weight": "1"},
                {"preference_type": "minimize_cut_waste", "weight": "2"},
            ]
        },
        {"preferences": [{"preference_type": "minimize_cut_waste", "weight": "1"}], "max_plans": 6},
    ],
)
def test_ranked_plan_request_contract_rejects_invalid_preferences(api, body):
    client, _ = api
    inputs(client, eligible=True)
    assert (
        client.post(
            BASE + "/tasks/TASK-001/ranked-plans", headers=headers("planner"), json=body
        ).status_code
        == 422
    )


def test_ranked_plan_permissions_scope_limits_and_openapi(api, monkeypatch):
    client, _ = api
    inputs(client, eligible=True)
    path = BASE + "/tasks/TASK-001/ranked-plans"
    body = {"preferences": [{"preference_type": "minimize_cut_waste", "weight": "1"}]}
    assert client.post(path, headers=headers("reader"), json=body).status_code == 403
    assert client.post(path, headers=headers("other"), json=body).status_code == 403
    assert (
        client.post(
            "/api/v1/projects/PRJ-MISSING/tasks/TASK-001/ranked-plans",
            headers=headers("planner"),
            json=body,
        ).status_code
        == 404
    )
    monkeypatch.setattr("material_allocation_copilot.application.ranking.MAX_RANKING_SLOTS", 0)
    limited = client.post(path, headers=headers("planner"), json=body)
    assert limited.status_code == 422
    assert limited.json()["error"]["context"]["maximum_slot_count"] == 0
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/projects/{project_id}/tasks/{task_id}/ranked-plans"][
        "post"
    ]
    assert operation["security"] == [{"HTTPBearer": []}]
    for name in ("PreferenceRequest", "RankedPlansResult", "RankedPlan", "CriterionScore"):
        assert name in schema["components"]["schemas"]
