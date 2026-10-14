"""Normalized CSV input: source hashes are generated, never supplied by the file."""

import csv
import hashlib
import json
from io import StringIO

from pydantic import ValidationError

from material_allocation_copilot.application.hashing import canonical_hash
from material_allocation_copilot.domain import MaterialItem, SlotRequirement
from material_allocation_copilot.errors import ApplicationError, ErrorCode


def _rows(data: bytes) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        headers = reader.fieldnames or []
        if not headers or len(headers) != len(set(headers)):
            raise ValueError("CSV headers must be present and unique")
        rows = list(reader)
        if not rows or len(rows) > 10000:
            raise ValueError("CSV must contain 1-10000 material rows")
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError("CSV row width must match its headers")
        return rows
    except (UnicodeError, csv.Error, ValueError) as error:
        raise ApplicationError(
            ErrorCode.INPUT_INVALID, "CSV table structure is invalid."
        ) from error


def inventory_from_csv(data: bytes, *, inventory_version_id: str) -> tuple[MaterialItem, ...]:
    rows = _rows(data)
    file_hash = hashlib.sha256(data).hexdigest()
    materials = []
    controlled = {
        "inventory_version_id",
        "source_system",
        "source_file_sha256",
        "source_row_sha256",
    }
    for number, row in enumerate(rows, start=2):
        try:
            if controlled.intersection(row):
                raise ValueError("CSV contains system-controlled provenance fields")
            values: dict[str, object] = {key: value for key, value in row.items() if value != ""}
            if "defects" in values:
                values["defects"] = json.loads(str(values["defects"]))
            values.update(
                inventory_version_id=inventory_version_id,
                source_system="normalized_csv",
                source_file_sha256=file_hash,
                source_row_sha256=canonical_hash(row),
            )
            materials.append(MaterialItem.model_validate(values))
        except (ValidationError, ValueError) as error:
            raise ApplicationError(
                ErrorCode.INPUT_INVALID,
                "CSV material row is invalid.",
                context={"row_number": number},
            ) from error
    return tuple(materials)


def slots_from_csv(
    data: bytes,
    *,
    project_id: str,
    slot_requirement_version_id: str,
    confirmed_by: str,
) -> tuple[SlotRequirement, ...]:
    rows = _rows(data)
    if len(rows) > 1000:
        raise ApplicationError(ErrorCode.INPUT_INVALID, "Slot CSV exceeds 1000 rows.")
    slots = []
    controlled = {"project_id", "slot_requirement_version_id", "confirmed_by", "source_row_sha256"}
    for number, row in enumerate(rows, start=2):
        try:
            if controlled.intersection(row):
                raise ValueError("CSV contains system-controlled confirmation fields")
            values: dict[str, object] = {key: value for key, value in row.items() if value != ""}
            values.update(
                project_id=project_id,
                slot_requirement_version_id=slot_requirement_version_id,
                confirmed_by=confirmed_by,
                source_row_sha256=canonical_hash(row),
            )
            slots.append(SlotRequirement.model_validate(values))
        except (ValidationError, ValueError) as error:
            raise ApplicationError(
                ErrorCode.INPUT_INVALID, "CSV slot row is invalid.", context={"row_number": number}
            ) from error
    return tuple(slots)
