"""Read-only review exports; no execution or inventory reservation is implied."""

import csv
from io import StringIO

from material_allocation_copilot.application.records import AllocationPlanRecord, ApprovalRecord


def _cell(value: object) -> str:
    text = str(value)
    # CSV may be opened in spreadsheet programs: do not execute cells as formulas.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def review_csv(plan: AllocationPlanRecord, approval: ApprovalRecord | None) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(
        [
            "allocation_plan_id",
            "plan_version",
            "slot_id",
            "material_id",
            "terminal_status",
            "approval_decision",
            "input_snapshot_hash",
            "export_purpose",
            "execution_authorized",
        ]
    )
    assignments = plan.payload["assignments"] or [{"slot_id": "", "material_id": ""}]
    for item in assignments:
        writer.writerow(
            [
                _cell(value)
                for value in (
                    plan.allocation_plan_id,
                    plan.plan_version,
                    item["slot_id"],
                    item["material_id"],
                    plan.terminal_status.value,
                    approval.decision.value if approval else "pending",
                    plan.input_snapshot_hash,
                    "review",
                    "false",
                )
            ]
        )
    return output.getvalue()
