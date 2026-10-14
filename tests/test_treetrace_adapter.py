import csv
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from material_allocation_copilot.adapters.treetrace import (
    DECISION_FIELD_ALLOWLIST,
    PROHIBITED_DECISION_FIELDS,
    TREETRACE_BOARDS_V1_SHA256,
    import_treetrace_boards,
)
from material_allocation_copilot.domain import (
    AvailabilityStatus,
    StructuralUseStatus,
)
from material_allocation_copilot.errors import ApplicationError, ErrorCode

LOCAL_BOARDS_PATH = (
    Path(__file__).resolve().parents[1] / ".local-data" / "TreeTrace" / "tables" / "boards.txt"
)
LEGACY_BOARDS_PATH = Path("D:/timber-match/data/raw/TreeTrace/tables/boards.txt")
REAL_BOARDS_PATH = LOCAL_BOARDS_PATH if LOCAL_BOARDS_PATH.exists() else LEGACY_BOARDS_PATH


def _source_row(
    board_id: str,
    *,
    grade_code: str = "2",
    length_m: str = "4.180",
    weak_point_m: str = "2.0",
) -> dict[str, str]:
    row = {field_name: "0" for field_name in DECISION_FIELD_ALLOWLIST}
    row.update(
        {
            "boardID": board_id,
            "length_m": length_m,
            "thickness_m": "0.0454",
            "width_m": "0.1442",
            "gradeNFB52001Whole": grade_code,
            "weakPointx_m": weak_point_m,
            "knotTypeWideFaceTested": "S2",
            "knotSizeWideFaceTested_mm": "44",
            "knotTypeWideFaceWhole": "S2",
            "knotSizeWideFaceWhole_mm": "44",
            "knotTypeNarrowFaceTested": "S0",
            "knotSizeNarrowFaceTested_mm": "14",
            "knotTypeNarrowFaceWhole": "S0",
            "knotSizeNarrowFaceWhole_mm": "14",
            "failurex_m": "2.5",
        }
    )
    return row


def _write_source(path: Path, rows: list[dict[str, str]]) -> str:
    field_names = sorted(set().union(*(row.keys() for row in rows)))
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=field_names, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_adapter_preserves_measurements_and_unknown_business_state(tmp_path: Path) -> None:
    source_path = tmp_path / "boards.txt"
    source_hash = _write_source(
        source_path,
        [_source_row("24"), _source_row("43", grade_code="4", weak_point_m="NA")],
    )

    result = import_treetrace_boards(
        source_path,
        inventory_version_id="INV-test-1",
        expected_source_sha256=source_hash,
        expected_row_count=2,
        imported_at=datetime(2026, 10, 4, 10, 0, tzinfo=UTC),
    )

    first, rejected = result.materials
    assert first.material_id == "TT-B024"
    assert str(first.section_width_mm) == "45.4000"
    assert first.measured_at is None
    assert first.availability_status is AvailabilityStatus.UNKNOWN
    assert first.strength_grade == "C24"
    assert first.structural_use_status is StructuralUseStatus.NOT_EVALUABLE
    assert rejected.structural_use_status is StructuralUseStatus.INELIGIBLE
    assert rejected.strength_grade is None
    assert result.source_evidence[1].weak_point_position_mm is None


def test_adapter_does_not_expose_prohibited_decision_values(tmp_path: Path) -> None:
    source_path = tmp_path / "boards.txt"
    source_hash = _write_source(source_path, [_source_row("1")])

    result = import_treetrace_boards(
        source_path,
        inventory_version_id="INV-test-2",
        expected_source_sha256=source_hash,
        expected_row_count=1,
    )
    serialized = result.materials[0].model_dump_json() + result.source_evidence[0].model_dump_json()

    assert "2.5" not in serialized
    assert all(field_name not in serialized for field_name in PROHIBITED_DECISION_FIELDS)


def test_adapter_rejects_hash_mismatch(tmp_path: Path) -> None:
    source_path = tmp_path / "boards.txt"
    _write_source(source_path, [_source_row("1")])

    with pytest.raises(ApplicationError) as captured:
        import_treetrace_boards(
            source_path,
            inventory_version_id="INV-test-3",
            expected_source_sha256="0" * 64,
            expected_row_count=1,
        )

    assert captured.value.code is ErrorCode.INPUT_INVALID
    assert "actual_sha256" in captured.value.context


def test_adapter_rejects_missing_required_measurement(tmp_path: Path) -> None:
    source_path = tmp_path / "boards.txt"
    row = _source_row("1", length_m="NA")
    source_hash = _write_source(source_path, [row])

    with pytest.raises(ApplicationError) as captured:
        import_treetrace_boards(
            source_path,
            inventory_version_id="INV-test-4",
            expected_source_sha256=source_hash,
            expected_row_count=1,
        )

    assert captured.value.code is ErrorCode.INPUT_INVALID
    assert captured.value.context["row_number"] == 2


@pytest.mark.parametrize("length_m", ["0", "-1", "NaN", "sNaN", "Infinity"])
def test_adapter_wraps_invalid_measurement_errors(tmp_path: Path, length_m: str) -> None:
    source_path = tmp_path / "boards.txt"
    source_hash = _write_source(source_path, [_source_row("1", length_m=length_m)])
    with pytest.raises(ApplicationError) as captured:
        import_treetrace_boards(
            source_path,
            inventory_version_id="INV-bad",
            expected_source_sha256=source_hash,
            expected_row_count=1,
        )
    assert captured.value.code is ErrorCode.INPUT_INVALID


def test_adapter_rejects_ids_that_collide_after_normalization(tmp_path: Path) -> None:
    source_path = tmp_path / "boards.txt"
    source_hash = _write_source(source_path, [_source_row("1"), _source_row("01")])
    with pytest.raises(ApplicationError) as captured:
        import_treetrace_boards(
            source_path,
            inventory_version_id="INV-collision",
            expected_source_sha256=source_hash,
            expected_row_count=2,
        )
    assert captured.value.code is ErrorCode.CONFLICT


@pytest.mark.parametrize(
    "case", ["duplicate_header", "malformed_header", "short_row", "empty_rows"]
)
def test_adapter_wraps_malformed_table_errors(tmp_path: Path, case: str) -> None:
    source_path = tmp_path / "boards.txt"
    _write_source(source_path, [_source_row("1")])
    lines = source_path.read_text(encoding="utf-8").splitlines()
    if case == "duplicate_header":
        text = lines[0] + "\tboardID\n" + lines[1] + "\t1\n"
    elif case == "malformed_header":
        text = '"unterminated header'
    elif case == "short_row":
        text = lines[0] + "\n1\n"
    else:
        text = lines[0] + "\n"
    source_path.write_text(text, encoding="utf-8")
    with pytest.raises(ApplicationError) as captured:
        import_treetrace_boards(
            source_path,
            inventory_version_id="INV-malformed",
            expected_source_sha256=None,
            expected_row_count=None,
        )
    assert captured.value.code is ErrorCode.INPUT_INVALID


@pytest.mark.skipif(not REAL_BOARDS_PATH.exists(), reason="local TreeTrace V1 source unavailable")
def test_real_treetrace_v1_imports_all_346_rows() -> None:
    result = import_treetrace_boards(
        REAL_BOARDS_PATH,
        inventory_version_id="INV-treetrace-v1-test",
    )

    assert result.source_sha256 == TREETRACE_BOARDS_V1_SHA256
    assert len(result.materials) == 346
    assert len({material.material_id for material in result.materials}) == 346
    board_24 = next(item for item in result.materials if item.material_id == "TT-B024")
    board_186_evidence = next(
        item for item in result.source_evidence if item.source_board_id == "186"
    )
    assert board_24.section_width_mm == Decimal("45.4000")
    assert board_186_evidence.weak_point_position_mm is None
    assert all(
        material.availability_status is AvailabilityStatus.UNKNOWN for material in result.materials
    )
