import csv
import hashlib
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from material_allocation_copilot.adapters import import_treetrace_boards
from material_allocation_copilot.application.ranking import (
    MAX_RANKING_EDGES,
    MAX_RANKING_SLOTS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_ROOT / ".local-data" / "TreeTrace" / "tables" / "boards.txt"
SAMPLE_PATH = PROJECT_ROOT / "sample-data" / "treetrace" / "treetrace-boards-demo.csv"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build-treetrace-sample.py"
HASH_MANIFEST = PROJECT_ROOT / "sample-data" / "treetrace" / "source-hashes.txt"
SOURCE_SHA256 = "6e8050e7bf8310136448c39f56c800fc7ddebd21199d8d421f8fd964da2c9121"
SAMPLE_SHA256 = "b29e90a4d2b797203581a22dfcd0f55aa9928f2cdf9a1a26d58bc99ca21b9f8b"
SOURCE_UNAVAILABLE = pytest.mark.skipif(
    not SOURCE_PATH.is_file(), reason="audited TreeTrace source is not stored in the repository"
)
EXPECTED_HEADER = [
    "material_id",
    "length_mm",
    "section_width_mm",
    "section_height_mm",
    "species",
    "knotTypeWideFaceWhole",
    "knotSizeWideFaceWhole_mm",
    "knotTypeNarrowFaceWhole",
    "knotSizeNarrowFaceWhole_mm",
]


def _sample_rows() -> list[dict[str, str]]:
    with SAMPLE_PATH.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        assert reader.fieldnames == EXPECTED_HEADER
        return list(reader)


def test_committed_treetrace_sample_matches_simple_ui_contract() -> None:
    rows = _sample_rows()

    assert len(rows) == 80
    assert len({row["material_id"] for row in rows}) == 80
    assert rows[0] == {
        "material_id": "TT-B001",
        "length_mm": "4179",
        "section_width_mm": "45.8",
        "section_height_mm": "144.2",
        "species": "Douglas fir",
        "knotTypeWideFaceWhole": "S2",
        "knotSizeWideFaceWhole_mm": "44",
        "knotTypeNarrowFaceWhole": "S0",
        "knotSizeNarrowFaceWhole_mm": "14",
    }
    for row in rows:
        assert row["material_id"].startswith("TT-B")
        assert row["species"] == "Douglas fir"
        assert Decimal(row["length_mm"]) > 0
        assert Decimal(row["section_width_mm"]) > 0
        assert Decimal(row["section_height_mm"]) > 0
        assert row["knotTypeWideFaceWhole"] in {"", "0"} | {
            f"{group}{condition}" for group in "SG" for condition in range(6)
        }
        assert row["knotTypeNarrowFaceWhole"] in {"", "0"} | {
            f"{group}{condition}" for group in "SG" for condition in range(6)
        }


def test_committed_sample_stays_within_ranking_budget() -> None:
    rows = _sample_rows()

    assert len(rows) * MAX_RANKING_SLOTS <= MAX_RANKING_EDGES


@SOURCE_UNAVAILABLE
def test_treetrace_sample_is_reproducible() -> None:
    with tempfile.NamedTemporaryFile(
        dir=SAMPLE_PATH.parent, suffix=".csv", delete=False
    ) as temporary_file:
        output_path = Path(temporary_file.name)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(BUILD_SCRIPT),
                "--source",
                str(SOURCE_PATH),
                "--output",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        assert "Wrote 80 rows" in completed.stdout
        assert output_path.read_bytes() == SAMPLE_PATH.read_bytes()
        assert (
            hashlib.sha256(output_path.read_bytes()).hexdigest()
            == hashlib.sha256(SAMPLE_PATH.read_bytes()).hexdigest()
        )
    finally:
        output_path.unlink(missing_ok=True)


def test_treetrace_sample_hash_manifest_matches_files() -> None:
    manifest = HASH_MANIFEST.read_text(encoding="utf-8")

    if SOURCE_PATH.is_file():
        assert hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest() == SOURCE_SHA256
    assert hashlib.sha256(SAMPLE_PATH.read_bytes()).hexdigest() == SAMPLE_SHA256
    assert f"{SOURCE_SHA256}  .local-data/TreeTrace/tables/boards.txt" in manifest
    assert f"{SAMPLE_SHA256}  sample-data/treetrace/treetrace-boards-demo.csv" in manifest


@SOURCE_UNAVAILABLE
def test_treetrace_sample_geometry_matches_read_only_adapter() -> None:
    rows = _sample_rows()
    imported = import_treetrace_boards(
        SOURCE_PATH,
        inventory_version_id="INV-treetrace-sample-verification",
    )

    assert len(imported.materials) == 346
    for row, material in zip(rows, imported.materials[: len(rows)], strict=True):
        assert row["material_id"] == material.material_id
        assert Decimal(row["length_mm"]) == material.length_mm
        assert Decimal(row["section_width_mm"]) == material.section_width_mm
        assert Decimal(row["section_height_mm"]) == material.section_height_mm
        assert row["species"] == material.species
        whole_knots = {item.face: item for item in material.surface_knots if item.scope == "whole"}
        if row["knotTypeWideFaceWhole"]:
            assert row["knotTypeWideFaceWhole"] == whole_knots["wide"].knot_type_code
        else:
            assert "wide" not in whole_knots
        if row["knotTypeNarrowFaceWhole"]:
            assert row["knotTypeNarrowFaceWhole"] == whole_knots["narrow"].knot_type_code
        else:
            assert "narrow" not in whole_knots
