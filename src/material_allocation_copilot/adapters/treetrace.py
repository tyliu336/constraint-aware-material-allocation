"""Read-only TreeTrace boards adapter with an explicit decision-field allowlist."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, DecimalException, InvalidOperation
from io import StringIO
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from material_allocation_copilot.domain import (
    AssessmentMethod,
    AvailabilityStatus,
    DefectDataStatus,
    DefectObservation,
    DefectSeverity,
    MaterialItem,
    ReuseHistoryStatus,
    StructuralUseStatus,
    SurfaceKnotObservation,
    TreatmentContaminationStatus,
)
from material_allocation_copilot.errors import ApplicationError, ErrorCode

TREETRACE_DATASET_ID = "doi:10.15454/YUNEGL"
TREETRACE_DATASET_VERSION = "V1"
TREETRACE_BOARDS_V1_SHA256 = "6e8050e7bf8310136448c39f56c800fc7ddebd21199d8d421f8fd964da2c9121"
TREETRACE_V1_ROW_COUNT = 346

GRADE_BY_SOURCE_CODE: dict[str, str | None] = {
    "1": "C30",
    "2": "C24",
    "3": "C18",
    "4": None,
}

DECISION_FIELD_ALLOWLIST = frozenset(
    {
        "boardID",
        "length_m",
        "thickness_m",
        "width_m",
        "gradeNFB52001Whole",
        "weakPointx_m",
        "knotTypeWideFaceTested",
        "knotSizeWideFaceTested_mm",
        "knotTypeWideFaceWhole",
        "knotSizeWideFaceWhole_mm",
        "knotTypeNarrowFaceTested",
        "knotSizeNarrowFaceTested_mm",
        "knotTypeNarrowFaceWhole",
        "knotSizeNarrowFaceWhole_mm",
    }
)

PROHIBITED_DECISION_FIELDS = frozenset(
    {
        "failurex_m",
        "bendingTestSuccess",
        "startloadMoE_N",
        "endLoadMoE_N",
        "localStatEWBendMoE_Pa",
        "globalStatEWBendMoE_Pa",
        "maxLoadStatEWBend_N",
        "strenghtEWBend_Pa",
        "gradeNFB52001Tested",
    }
)

_KNOT_FIELDS = (
    ("wide", "tested", "knotTypeWideFaceTested", "knotSizeWideFaceTested_mm"),
    ("wide", "whole", "knotTypeWideFaceWhole", "knotSizeWideFaceWhole_mm"),
    ("narrow", "tested", "knotTypeNarrowFaceTested", "knotSizeNarrowFaceTested_mm"),
    ("narrow", "whole", "knotTypeNarrowFaceWhole", "knotSizeNarrowFaceWhole_mm"),
)


class _FrozenSourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TreeTraceKnotObservation(_FrozenSourceModel):
    face: Literal["wide", "narrow"]
    scope: Literal["tested", "whole"]
    knot_type: str | None
    size_mm: Annotated[Decimal, Field(ge=0)] | None
    source_type_field: str
    source_size_field: str


class TreeTraceSourceEvidence(_FrozenSourceModel):
    source_board_id: str
    source_grade_code: Literal["1", "2", "3", "4"]
    source_row_sha256: str
    weak_point_position_mm: Annotated[Decimal, Field(ge=0)] | None
    weak_point_position_ratio: Annotated[Decimal, Field(ge=0, le=1)] | None
    knot_observations: tuple[TreeTraceKnotObservation, ...]


@dataclass(frozen=True, slots=True)
class TreeTraceImportResult:
    source_path: Path
    source_sha256: str
    imported_at: datetime
    inventory_version_id: str
    materials: tuple[MaterialItem, ...]
    source_evidence: tuple[TreeTraceSourceEvidence, ...]


def _canonical_row_sha256(row: dict[str, str]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _required_decimal(row: dict[str, str], field_name: str) -> Decimal:
    raw_value = row[field_name]
    if raw_value in {"", "NA"}:
        raise ValueError(f"{field_name} is missing")
    try:
        value = Decimal(raw_value)
    except InvalidOperation as error:
        raise ValueError(f"{field_name} is not a decimal") from error
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name} must be a finite positive measurement")
    return value


def _optional_decimal(row: dict[str, str], field_name: str) -> Decimal | None:
    raw_value = row[field_name]
    if raw_value in {"", "NA"}:
        return None
    try:
        value = Decimal(raw_value)
    except InvalidOperation as error:
        raise ValueError(f"{field_name} is not a decimal") from error
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def _optional_text(row: dict[str, str], field_name: str) -> str | None:
    raw_value = row[field_name].strip()
    return None if raw_value in {"", "NA"} else raw_value


def _material_id(source_board_id: str) -> str:
    if not source_board_id or not source_board_id.isascii() or not source_board_id.isdigit():
        raise ValueError("boardID must be a non-negative ASCII integer string")
    return f"TT-B{int(source_board_id):03d}"


def _build_source_evidence(row: dict[str, str]) -> TreeTraceSourceEvidence:
    length_m = _required_decimal(row, "length_m")
    weak_point_m = _optional_decimal(row, "weakPointx_m")
    weak_point_ratio = weak_point_m / length_m if weak_point_m is not None else None
    knots = tuple(
        TreeTraceKnotObservation(
            face=face,
            scope=scope,
            knot_type=_optional_text(row, type_field),
            size_mm=_optional_decimal(row, size_field),
            source_type_field=type_field,
            source_size_field=size_field,
        )
        for face, scope, type_field, size_field in _KNOT_FIELDS
    )
    return TreeTraceSourceEvidence(
        source_board_id=row["boardID"],
        source_grade_code=row["gradeNFB52001Whole"],
        source_row_sha256=_canonical_row_sha256(row),
        weak_point_position_mm=(weak_point_m * Decimal(1000) if weak_point_m is not None else None),
        weak_point_position_ratio=weak_point_ratio,
        knot_observations=knots,
    )


def _build_defect_observations(
    evidence: TreeTraceSourceEvidence,
) -> tuple[DefectObservation, ...]:
    source_prefix = f"treetrace:{evidence.source_board_id}"
    observations = [
        DefectObservation(
            defect_type="observed_weak_point",
            severity=DefectSeverity.UNKNOWN,
            position_ratio=evidence.weak_point_position_ratio,
            source_ref=f"{source_prefix}:weakPointx_m",
            note="Observed weak-point position; compatible major/minor severity is unavailable.",
        )
    ]
    observations.extend(
        DefectObservation(
            defect_type=f"source_knot_{knot.face}_face",
            severity=DefectSeverity.UNKNOWN,
            position_ratio=None,
            source_ref=f"{source_prefix}:{knot.source_type_field}+{knot.source_size_field}",
            note=(
                f"TreeTrace knot type={knot.knot_type}, size_mm={knot.size_mm}, scope={knot.scope}."
            ),
        )
        for knot in evidence.knot_observations
    )
    return tuple(observations)


def _build_surface_knot_observations(
    evidence: TreeTraceSourceEvidence,
) -> tuple[SurfaceKnotObservation, ...]:
    source_prefix = f"treetrace:{evidence.source_board_id}"
    return tuple(
        SurfaceKnotObservation(
            face=knot.face,
            scope=knot.scope,
            knot_type_code=knot.knot_type,
            size_mm=knot.size_mm,
            source_ref=f"{source_prefix}:{knot.source_type_field}+{knot.source_size_field}",
        )
        for knot in evidence.knot_observations
        if knot.knot_type is not None
    )


def _build_material(
    row: dict[str, str],
    *,
    evidence: TreeTraceSourceEvidence,
    inventory_version_id: str,
    source_file_sha256: str,
) -> MaterialItem:
    grade_code = evidence.source_grade_code
    mapped_grade = GRADE_BY_SOURCE_CODE[grade_code]
    structural_status = (
        StructuralUseStatus.INELIGIBLE if grade_code == "4" else StructuralUseStatus.NOT_EVALUABLE
    )
    source_board_id = evidence.source_board_id

    return MaterialItem(
        material_id=_material_id(source_board_id),
        inventory_version_id=inventory_version_id,
        source_system="treetrace_douglas",
        source_record_id=source_board_id,
        source_file_sha256=source_file_sha256,
        source_row_sha256=evidence.source_row_sha256,
        measured_at=None,
        length_mm=_required_decimal(row, "length_m") * Decimal(1000),
        section_width_mm=_required_decimal(row, "thickness_m") * Decimal(1000),
        section_height_mm=_required_decimal(row, "width_m") * Decimal(1000),
        source_asset_ref=(
            f"{TREETRACE_DATASET_ID}/{TREETRACE_DATASET_VERSION}#boardID={source_board_id}"
        ),
        species="Douglas fir",
        structural_use_status=structural_status,
        strength_grade=mapped_grade,
        assessment_method=AssessmentMethod.UNKNOWN,
        defect_data_status=DefectDataStatus.PARTIAL,
        defect_evidence_ref=f"treetrace:{source_board_id}:defect-observations",
        defects=_build_defect_observations(evidence),
        surface_knots=_build_surface_knot_observations(evidence),
        reuse_history_status=ReuseHistoryStatus.UNKNOWN,
        treatment_contamination_status=TreatmentContaminationStatus.UNKNOWN,
        availability_status=AvailabilityStatus.UNKNOWN,
    )


def import_treetrace_boards(
    boards_path: str | Path,
    *,
    inventory_version_id: str,
    expected_source_sha256: str | None = TREETRACE_BOARDS_V1_SHA256,
    expected_row_count: int | None = TREETRACE_V1_ROW_COUNT,
    imported_at: datetime | None = None,
) -> TreeTraceImportResult:
    """Import a TreeTrace board table without modifying or copying the source file."""

    source_path = Path(boards_path).resolve()
    if not source_path.is_file():
        raise ApplicationError(
            ErrorCode.NOT_FOUND,
            "TreeTrace boards file was not found.",
            context={"path": str(source_path)},
        )

    try:
        source_bytes = source_path.read_bytes()
        source_text = source_bytes.decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "TreeTrace source could not be read as UTF-8.",
            context={"path": str(source_path)},
        ) from error
    # Hash and parse exactly the same bytes, even if the source changes during import.
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if expected_source_sha256 is not None and source_sha256 != expected_source_sha256:
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "TreeTrace source hash does not match the expected dataset version.",
            context={
                "path": str(source_path),
                "expected_sha256": expected_source_sha256,
                "actual_sha256": source_sha256,
            },
        )

    with StringIO(source_text, newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", strict=True)
        try:
            headers = reader.fieldnames or ()
        except csv.Error as error:
            raise ApplicationError(
                ErrorCode.INPUT_INVALID, "TreeTrace source headers are malformed."
            ) from error
        if len(headers) != len(set(headers)):
            raise ApplicationError(
                ErrorCode.INPUT_INVALID, "TreeTrace source has duplicate headers."
            )
        source_fields = set(headers)
        missing_fields = sorted(DECISION_FIELD_ALLOWLIST - source_fields)
        if missing_fields:
            raise ApplicationError(
                ErrorCode.INPUT_INVALID,
                "TreeTrace source is missing required allowlisted fields.",
                context={"missing_fields": missing_fields},
            )
        try:
            rows = list(reader)
        except csv.Error as error:
            raise ApplicationError(
                ErrorCode.INPUT_INVALID, "TreeTrace source is malformed."
            ) from error
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ApplicationError(
            ErrorCode.INPUT_INVALID, "TreeTrace row width does not match headers."
        )
    if not rows:
        raise ApplicationError(
            ErrorCode.INPUT_INVALID, "TreeTrace source contains no material rows."
        )

    if expected_row_count is not None and len(rows) != expected_row_count:
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "TreeTrace row count does not match the expected dataset version.",
            context={"expected_row_count": expected_row_count, "actual_row_count": len(rows)},
        )

    board_ids = [row["boardID"] for row in rows]
    if len(board_ids) != len(set(board_ids)):
        raise ApplicationError(
            ErrorCode.CONFLICT,
            "TreeTrace boardID values must be unique.",
        )

    materials: list[MaterialItem] = []
    source_evidence: list[TreeTraceSourceEvidence] = []
    material_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        try:
            grade_code = row["gradeNFB52001Whole"]
            if grade_code not in GRADE_BY_SOURCE_CODE:
                raise ValueError("gradeNFB52001Whole must be one of 1, 2, 3, or 4")
            evidence = _build_source_evidence(row)
            material = _build_material(
                row,
                evidence=evidence,
                inventory_version_id=inventory_version_id,
                source_file_sha256=source_sha256,
            )
            if material.material_id in material_ids:
                raise ApplicationError(
                    ErrorCode.CONFLICT, "TreeTrace boardIDs collide after ID normalization."
                )
        except (KeyError, ValueError, ValidationError, DecimalException) as error:
            raise ApplicationError(
                ErrorCode.INPUT_INVALID,
                "TreeTrace row could not be normalized.",
                context={
                    "row_number": row_number,
                    "source_board_id": row.get("boardID"),
                    "detail": str(error),
                },
            ) from error
        source_evidence.append(evidence)
        materials.append(material)
        material_ids.add(material.material_id)

    import_timestamp = imported_at or datetime.now(UTC)
    if import_timestamp.tzinfo is None or import_timestamp.utcoffset() is None:
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "imported_at must include a timezone offset.",
        )

    return TreeTraceImportResult(
        source_path=source_path,
        source_sha256=source_sha256,
        imported_at=import_timestamp,
        inventory_version_id=inventory_version_id,
        materials=tuple(materials),
        source_evidence=tuple(source_evidence),
    )
