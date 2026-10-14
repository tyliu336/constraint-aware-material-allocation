"""Read-only adapters for external material sources."""

from material_allocation_copilot.adapters.treetrace import (
    TREETRACE_BOARDS_V1_SHA256,
    TreeTraceImportResult,
    TreeTraceSourceEvidence,
    import_treetrace_boards,
)

__all__ = [
    "TREETRACE_BOARDS_V1_SHA256",
    "TreeTraceImportResult",
    "TreeTraceSourceEvidence",
    "import_treetrace_boards",
]
