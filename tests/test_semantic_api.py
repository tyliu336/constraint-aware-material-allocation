from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from material_allocation_copilot.api.app import create_app
from material_allocation_copilot.api.auth import CredentialEntry, CredentialStore
from material_allocation_copilot.application.access import Principal
from material_allocation_copilot.config import AppConfig, DeploymentEnvironment

ROOT = Path(__file__).resolve().parents[1]
BASE = "/api/v1/projects/PRJ-001"
TOKENS = {
    "planner": "semantic-planner",
    "reviewer": "semantic-reviewer",
    "reader": "semantic-reader",
}


class ScriptedRequirementModel:
    model_id = "fixture-model"
    available = True

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def generate_structured(self, *, prompt, user_input, output_type):
        del prompt
        if output_type.__name__ == "ModelPreferenceInterpretation":
            if "瑕疵" in user_input or "节疤" in user_input:
                return {
                    "summary": "优先选择已记录表面瑕疵较少的木料。",
                    "preferences": [
                        {
                            "preference_type": "minimize_surface_defects",
                            "weight": 10,
                            "rationale": "用户明确要求减少表面瑕疵和节疤。",
                        }
                    ],
                }
            return {
                "summary": "优先减少余料，同时保留长料。",
                "preferences": [
                    {
                        "preference_type": "minimize_cut_waste",
                        "weight": 9,
                        "rationale": "用户首先希望减少切割余料。",
                    },
                    {
                        "preference_type": "preserve_scarce_long_stock",
                        "weight": 6,
                        "rationale": "用户也希望保留稀缺长料。",
                    },
                ],
            }
        return self.outputs.pop(0)


def output():
    return {
        "summary": "One unconfirmed beam requirement.",
        "slots": [
            {
                "slot_id": "SLOT-B01",
                "slot_role": "beam",
                "clear_span_mm": 3000,
                "end_allowance_mm": 100,
                "min_section_width_mm": 90,
                "min_section_height_mm": 190,
                "allow_section_rotation": False,
                "minimum_strength_grade": "C24",
                "capacity_requirement_ref": None,
                "orientation_requirement": "fixed",
                "defect_restriction_profile_id": None,
                "max_moisture_content_pct": None,
                "treatment_restriction_profile_id": None,
                "visible_surface_scope": "none",
                "aesthetic_requirement_text": None,
                "visual_reference_ref": None,
                "rule_set_version": "rules-0.1.1",
                "requirement_source_ref": "DWG-S201#B01",
            }
        ],
        "soft_preferences": [
            {
                "preference_type": "minimize_cut_waste",
                "requested_weight": 2,
                "scope": "global",
                "rationale": "Prefer higher geometric length utilization.",
            }
        ],
        "issues": [],
        "clarification_questions": [],
    }


def headers(role: str, key: str | None = None):
    result = {"Authorization": f"Bearer {TOKENS[role]}"}
    if key:
        result["Idempotency-Key"] = key
    return result


@pytest.fixture
def semantic_api(tmp_path: Path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'semantic-api.db').as_posix()}"
    monkeypatch.setenv("MAC_ENVIRONMENT", "test")
    monkeypatch.setenv("MAC_TEST_DATABASE_URL", database_url)
    migration = Config(str(ROOT / "alembic.ini"))
    command.upgrade(migration, "head")
    credentials = CredentialStore(
        tuple(
            CredentialEntry(
                token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                principal=Principal(
                    user_id=f"USER-{role}",
                    roles=frozenset({role}),
                    project_ids=frozenset({"PRJ-001"}),
                ),
            )
            for role, token in TOKENS.items()
        )
    )
    model = ScriptedRequirementModel([output()])
    app = create_app(
        AppConfig(environment=DeploymentEnvironment.TEST, database_url=database_url),
        credentials=credentials,
        requirement_model=model,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/projects",
            headers=headers("planner"),
            json={"project_id": "PRJ-001", "name": "Semantic API"},
        )
        assert response.status_code == 201
        yield client


def confirmation_body():
    return {
        "requirement_id": "REQ-DRAFT-001",
        "slot_requirement_version_id": "SLOTS-v2",
        "slots": [
            {
                "slot_id": "SLOT-B01",
                "slot_role": "beam",
                "clear_span_mm": 3000,
                "end_allowance_mm": 100,
                "min_section_width_mm": 90,
                "min_section_height_mm": 190,
                "allow_section_rotation": False,
                "minimum_strength_grade": "C24",
                "orientation_requirement": "fixed",
                "rule_set_version": "rules-0.1.1",
                "requirement_source_ref": "DWG-S201#B01",
            }
        ],
        "soft_preferences": [
            {
                "preference_type": "minimize_cut_waste",
                "weight": 2,
                "scope": "global",
                "rationale": "Prefer higher geometric length utilization.",
            }
        ],
    }


def test_requirement_draft_api_enforces_roles_and_confirmation_boundary(semantic_api):
    client = semantic_api
    reader_denied = client.post(
        f"{BASE}/requirement-drafts",
        headers=headers("reader", "draft-reader"),
        json={"draft_id": "DRAFT-X", "natural_language": "one beam"},
    )
    assert reader_denied.status_code == 403

    created = client.post(
        f"{BASE}/requirement-drafts",
        headers=headers("planner", "draft-create-001"),
        json={
            "draft_id": "DRAFT-001",
            "natural_language": "Allocate one C24 beam from drawing S201.",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "ready_for_confirmation"
    assert created.json()["confirmed_requirement_id"] is None
    assert len(created.json()["context_snapshot_ids"]) == 1
    assert len(created.json()["context_snapshot_hash"]) == 64

    fetched = client.get(f"{BASE}/requirement-drafts/DRAFT-001", headers=headers("reader"))
    assert fetched.status_code == 200
    assert fetched.json()["revision"] == 1

    planner_denied = client.post(
        f"{BASE}/requirement-drafts/DRAFT-001/confirm",
        headers=headers("planner", "confirm-planner"),
        json=confirmation_body(),
    )
    assert planner_denied.status_code == 403

    confirmed = client.post(
        f"{BASE}/requirement-drafts/DRAFT-001/confirm",
        headers=headers("reviewer", "confirm-001"),
        json=confirmation_body(),
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["confirmed_by"] == "USER-reviewer"
    assert confirmed.json()["soft_preferences"][0]["weight"] == "2"

    final_draft = client.get(f"{BASE}/requirement-drafts/DRAFT-001", headers=headers("reader"))
    assert final_draft.json()["status"] == "confirmed"
    assert final_draft.json()["confirmed_requirement_id"] == "REQ-DRAFT-001"


def test_prompt_registry_exposes_versions_and_hashes_but_not_prompt_text(semantic_api):
    response = semantic_api.get("/api/v1/prompts", headers=headers("reader"))
    assert response.status_code == 200
    assert {item["name"] for item in response.json()} == {
        "requirement_parser",
        "clarification_generator",
        "requirement_explanation",
        "preference_interpreter",
        "tool_orchestrator",
    }
    assert all(len(item["content_sha256"]) == 64 for item in response.json())
    assert all("system_prompt" not in item for item in response.json())


def test_preference_interpretation_uses_model_and_enforces_planner_role(semantic_api):
    denied = semantic_api.post(
        f"{BASE}/preference-interpretations",
        headers=headers("reader"),
        json={"prompt": "尽量省料"},
    )
    assert denied.status_code == 403

    response = semantic_api.post(
        f"{BASE}/preference-interpretations",
        headers=headers("planner"),
        json={"prompt": "优先减少余料，也尽量保留长料"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model_id"] == "fixture-model"
    assert payload["summary"] == "优先减少余料，同时保留长料。"
    assert [item["preference_type"] for item in payload["preferences"]] == [
        "minimize_cut_waste",
        "preserve_scarce_long_stock",
    ]
    assert payload["preferences"][0]["weight"] == 9
    assert len(payload["prompt_content_sha256"]) == 64


def test_preference_interpretation_understands_surface_defect_language(semantic_api):
    response = semantic_api.post(
        f"{BASE}/preference-interpretations",
        headers=headers("planner"),
        json={"prompt": "优先选择表面瑕疵少、节疤小的木料"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["prompt_version"] == "1.1.0"
    assert payload["preferences"] == [
        {
            "preference_type": "minimize_surface_defects",
            "weight": 10.0,
            "rationale": "用户明确要求减少表面瑕疵和节疤。",
        }
    ]
