"""Canonical content and task input snapshot identities."""

import hashlib
import json

from material_allocation_copilot.application.records import (
    AllocationTaskRecord,
    InventoryVersionRecord,
    RequirementRecord,
)


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def task_input_snapshot_hash(
    task: AllocationTaskRecord,
    inventory: InventoryVersionRecord,
    requirement: RequirementRecord,
) -> str:
    return canonical_hash(
        {
            "schema": "task-input-snapshot-v1",
            "project_id": task.project_id,
            "inventory_version_id": inventory.inventory_version_id,
            "inventory_snapshot_hash": inventory.snapshot_hash,
            "requirement_id": requirement.requirement_id,
            "requirement_content_hash": requirement.content_hash,
        }
    )
