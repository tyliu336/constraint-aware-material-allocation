"""Run a synthetic, temporary live OpenAI smoke test without persisting credentials.

The caller must provide ``MAC_OPENAI_API_KEY`` in the process environment. This
script never prints it and uses only synthetic inventory and requirement data.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from openai import OpenAI

from material_allocation_copilot.adapters.openai_agent import OpenAIAgentModel
from material_allocation_copilot.adapters.openai_requirements import OpenAIRequirementModel
from material_allocation_copilot.application import (
    ConfirmedRequirementInput,
    InventoryImportInput,
    InventoryService,
    PreferenceInterpretationInput,
    ProjectService,
    RequirementService,
    TaskService,
)
from material_allocation_copilot.application.access import Principal, Role
from material_allocation_copilot.application.agent_runtime import (
    AgentBudget,
    AgentRunInput,
    BoundedAgentOrchestrator,
)
from material_allocation_copilot.application.preference_interpretation import (
    PreferenceInterpretationService,
)
from material_allocation_copilot.domain import (
    AssessmentMethod,
    AvailabilityStatus,
    DefectDataStatus,
    MaterialItem,
    OrientationRequirement,
    SlotRequirement,
    StructuralUseStatus,
)
from material_allocation_copilot.persistence import (
    SqlAlchemyUnitOfWork,
    create_db_engine,
    create_session_factory,
)
from material_allocation_copilot.prompts import default_prompt_registry

ROOT = Path(__file__).resolve().parents[1]
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _seed(uow_factory) -> Principal:
    material = MaterialItem.model_validate(
        {
            "material_id": "MAT-SYNTHETIC-001",
            "inventory_version_id": "INV-SYNTHETIC-001",
            "source_system": "synthetic_live_smoke",
            "source_record_id": "ROW-SYNTHETIC-001",
            "source_file_sha256": HASH_A,
            "source_row_sha256": HASH_B,
            "measured_at": datetime(2026, 10, 10, 9, 0, tzinfo=UTC),
            "length_mm": "4000",
            "section_width_mm": "100",
            "section_height_mm": "200",
            "structural_use_status": StructuralUseStatus.ELIGIBLE,
            "strength_grade": "C24",
            "assessment_method": AssessmentMethod.ENGINEERING_ASSESSMENT,
            "assessor_id": "SYNTHETIC-ASSESSOR",
            "assessment_date": date(2026, 10, 10),
            "assessment_evidence_ref": "SYNTHETIC-EVIDENCE-001",
            "defect_data_status": DefectDataStatus.COMPLETE,
            "defect_evidence_ref": "SYNTHETIC-SCAN-001",
            "defects": [],
            "availability_status": AvailabilityStatus.AVAILABLE,
        }
    )
    slot = SlotRequirement.model_validate(
        {
            "project_id": "PRJ-SYNTHETIC-001",
            "slot_requirement_version_id": "SLOTS-SYNTHETIC-v1",
            "slot_id": "SLOT-SYNTHETIC-001",
            "slot_role": "beam",
            "clear_span_mm": "3000",
            "end_allowance_mm": "100",
            "min_section_width_mm": "90",
            "min_section_height_mm": "190",
            "allow_section_rotation": False,
            "minimum_strength_grade": "C24",
            "orientation_requirement": OrientationRequirement.FIXED,
            "rule_set_version": "rules-0.1.1",
            "requirement_source_ref": "SYNTHETIC-DRAWING-001",
            "confirmed_by": "SYNTHETIC-REVIEWER",
            "confirmed_at": datetime(2026, 10, 10, 9, 30, tzinfo=UTC),
            "source_row_sha256": HASH_C,
        }
    )
    ProjectService(uow_factory).create_project(
        project_id="PRJ-SYNTHETIC-001", name="Synthetic live API smoke"
    )
    InventoryService(uow_factory).import_inventory(
        InventoryImportInput(
            project_id="PRJ-SYNTHETIC-001",
            inventory_version_id="INV-SYNTHETIC-001",
            idempotency_key="synthetic-import-001",
            source_system="synthetic_live_smoke",
            source_path="synthetic/inventory.json",
            source_sha256=HASH_A,
            imported_at=datetime(2026, 10, 10, 9, 5, tzinfo=UTC),
            materials=(material,),
        )
    )
    RequirementService(uow_factory).save_confirmed(
        ConfirmedRequirementInput(
            requirement_id="REQ-SYNTHETIC-001",
            project_id="PRJ-SYNTHETIC-001",
            idempotency_key="synthetic-requirement-001",
            slots=(slot,),
        )
    )
    TaskService(uow_factory).create_task(
        allocation_task_id="TASK-SYNTHETIC-001",
        project_id="PRJ-SYNTHETIC-001",
        inventory_version_id="INV-SYNTHETIC-001",
        requirement_id="REQ-SYNTHETIC-001",
        idempotency_key="synthetic-task-001",
    )
    return Principal(
        user_id="SYNTHETIC-PLANNER",
        roles=frozenset({Role.PLANNER}),
        project_ids=frozenset({"PRJ-SYNTHETIC-001"}),
    )


def main() -> None:
    api_key = os.environ.get("MAC_OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MAC_OPENAI_API_KEY is required in process memory.")
    model_id = os.environ.get("MAC_REQUIREMENT_MODEL", "gpt-6.1-sol").strip()
    if not model_id:
        raise SystemExit("MAC_REQUIREMENT_MODEL must not be empty.")

    with tempfile.TemporaryDirectory(prefix="mac-live-smoke-") as temporary_directory:
        database_path = Path(temporary_directory) / "smoke.db"
        database_url = f"sqlite:///{database_path.as_posix()}"
        previous_environment = os.environ.get("MAC_ENVIRONMENT")
        previous_database = os.environ.get("MAC_TEST_DATABASE_URL")
        previous_api_key = os.environ.pop("MAC_OPENAI_API_KEY", None)
        try:
            os.environ["MAC_ENVIRONMENT"] = "test"
            os.environ["MAC_TEST_DATABASE_URL"] = database_url
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        finally:
            if previous_environment is None:
                os.environ.pop("MAC_ENVIRONMENT", None)
            else:
                os.environ["MAC_ENVIRONMENT"] = previous_environment
            if previous_database is None:
                os.environ.pop("MAC_TEST_DATABASE_URL", None)
            else:
                os.environ["MAC_TEST_DATABASE_URL"] = previous_database
            if previous_api_key is not None:
                os.environ["MAC_OPENAI_API_KEY"] = previous_api_key

        engine = create_db_engine(database_url)
        session_factory = create_session_factory(engine)

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(session_factory)

        try:
            principal = _seed(uow_factory)
            openai_client = OpenAI(api_key=api_key)
            preference_result = PreferenceInterpretationService(
                uow_factory,
                OpenAIRequirementModel(openai_client, model_id),
                default_prompt_registry(),
            ).interpret(
                PreferenceInterpretationInput(
                    project_id="PRJ-SYNTHETIC-001",
                    prompt="优先选择表面瑕疵较少、节疤尺寸更小的木料。",
                )
            )
            if not any(
                item.preference_type == "minimize_surface_defects"
                for item in preference_result.preferences
            ):
                raise SystemExit("model did not recognize the surface-defect preference")
            model = OpenAIAgentModel(
                openai_client,
                model_id,
                input_cost_per_million_usd=Decimal("2"),
                output_cost_per_million_usd=Decimal("10"),
            )
            result = BoundedAgentOrchestrator(uow_factory, model).run(
                principal,
                "PRJ-SYNTHETIC-001",
                AgentRunInput(
                    run_id="RUN-SYNTHETIC-LIVE-001",
                    allocation_task_id="TASK-SYNTHETIC-001",
                    goal=(
                        "Call check_plan_feasibility exactly once for this synthetic task. "
                        "Then call finish_agent_run with status success and a concise evidence "
                        "summary. Do not call other tools or request any write action."
                    ),
                    budget=AgentBudget(
                        max_model_turns=3,
                        max_tool_calls=2,
                        max_elapsed_ms=60_000,
                        max_total_tokens=8_000,
                        max_cost_microusd=100_000,
                        max_output_tokens_per_turn=800,
                    ),
                ),
            )
            print(
                json.dumps(
                    {
                        "model": result.run.model_id,
                        "preference_interpretation": {
                            "summary": preference_result.summary,
                            "preferences": [
                                item.model_dump(mode="json")
                                for item in preference_result.preferences
                            ],
                            "prompt_version": preference_result.prompt_version,
                            "prompt_content_sha256": (preference_result.prompt_content_sha256),
                        },
                        "status": result.run.status.value,
                        "error_code": result.run.error_code,
                        "usage": result.run.usage_payload,
                        "trace_events": [item.event_type for item in result.trace],
                        "tool_names": [
                            item.payload.get("tool_name")
                            for item in result.trace
                            if item.event_type == "tool_result"
                        ],
                        "temporary_database_removed_on_exit": True,
                        "synthetic_data_only": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            if result.run.status.value != "success":
                raise SystemExit(1)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
