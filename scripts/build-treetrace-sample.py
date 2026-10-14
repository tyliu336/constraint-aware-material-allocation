"""Build the GitHub-safe TreeTrace geometry sample used by the simple web UI."""

from __future__ import annotations

import argparse
import csv
import hashlib
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / ".local-data" / "TreeTrace" / "tables" / "boards.txt"
DEFAULT_OUTPUT = PROJECT_ROOT / "sample-data" / "treetrace" / "treetrace-boards-demo.csv"

SOURCE_SHA256 = "6e8050e7bf8310136448c39f56c800fc7ddebd21199d8d421f8fd964da2c9121"
EXPECTED_ROW_COUNT = 346
KNOT_FIELDS = (
    "knotTypeWideFaceWhole",
    "knotSizeWideFaceWhole_mm",
    "knotTypeNarrowFaceWhole",
    "knotSizeNarrowFaceWhole_mm",
)
SOURCE_FIELDS = ("boardID", "length_m", "thickness_m", "width_m", *KNOT_FIELDS)
OUTPUT_FIELDS = (
    "material_id",
    "length_mm",
    "section_width_mm",
    "section_height_mm",
    "species",
    *KNOT_FIELDS,
)


def _positive_decimal(row: dict[str, str], field_name: str) -> Decimal:
    try:
        value = Decimal(row[field_name])
    except (KeyError, InvalidOperation) as error:
        raise ValueError(f"{field_name} must be a decimal") from error
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name} must be a finite positive measurement")
    return value


def _millimetres(value: Decimal) -> str:
    converted = value * Decimal(1000)
    rendered = format(converted, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _material_id(source_board_id: str) -> str:
    if not source_board_id or not source_board_id.isascii() or not source_board_id.isdigit():
        raise ValueError("boardID must be a non-negative ASCII integer string")
    return f"TT-B{int(source_board_id):03d}"


def build_rows(source_bytes: bytes) -> list[dict[str, str]]:
    actual_hash = hashlib.sha256(source_bytes).hexdigest()
    if actual_hash != SOURCE_SHA256:
        raise ValueError(
            "boards.txt does not match the audited TreeTrace V1 source: "
            f"expected {SOURCE_SHA256}, got {actual_hash}"
        )

    with StringIO(source_bytes.decode("utf-8-sig"), newline="") as source:
        reader = csv.DictReader(source, delimiter="\t", strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise ValueError("boards.txt contains duplicate headers")
        missing_fields = sorted(set(SOURCE_FIELDS) - set(headers))
        if missing_fields:
            raise ValueError(f"boards.txt is missing fields: {', '.join(missing_fields)}")
        source_rows = list(reader)

    if len(source_rows) != EXPECTED_ROW_COUNT:
        raise ValueError(
            f"boards.txt must contain {EXPECTED_ROW_COUNT} records, got {len(source_rows)}"
        )
    if any(None in row or any(value is None for value in row.values()) for row in source_rows):
        raise ValueError("boards.txt contains a row whose width does not match the headers")

    output_rows: list[dict[str, str]] = []
    material_ids: set[str] = set()
    for row_number, row in enumerate(source_rows, start=2):
        try:
            material_id = _material_id(row["boardID"])
            if material_id in material_ids:
                raise ValueError(f"duplicate normalized material_id {material_id}")
            output_rows.append(
                {
                    "material_id": material_id,
                    "length_mm": _millimetres(_positive_decimal(row, "length_m")),
                    "section_width_mm": _millimetres(_positive_decimal(row, "thickness_m")),
                    "section_height_mm": _millimetres(_positive_decimal(row, "width_m")),
                    "species": "Douglas fir",
                    **{field: row[field] for field in KNOT_FIELDS},
                }
            )
            material_ids.add(material_id)
        except ValueError as error:
            raise ValueError(
                f"invalid TreeTrace record at source row {row_number}: {error}"
            ) from error
    return output_rows


def render_csv(rows: list[dict[str, str]]) -> bytes:
    target = StringIO(newline="")
    writer = csv.DictWriter(target, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return target.getvalue().encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert audited TreeTrace V1 board geometry to the simple UI CSV contract."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--limit",
        type=int,
        default=80,
        help="Keep the first N source records; defaults to the 80-record public demo.",
    )
    args = parser.parse_args()

    source_bytes = args.source.read_bytes()
    rows = build_rows(source_bytes)
    if args.limit is not None:
        if args.limit < 1 or args.limit > EXPECTED_ROW_COUNT:
            parser.error(f"--limit must be between 1 and {EXPECTED_ROW_COUNT}")
        rows = rows[: args.limit]
    output_bytes = render_csv(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output_bytes)
    output_hash = hashlib.sha256(output_bytes).hexdigest()
    print(f"Wrote {len(rows)} rows to {args.output}")
    print(f"SHA-256 {output_hash}")


if __name__ == "__main__":
    main()
