"""Controlled domain vocabulary."""

from enum import StrEnum


class StructuralUseStatus(StrEnum):
    ELIGIBLE = "eligible"
    RESTRICTED = "restricted"
    INELIGIBLE = "ineligible"
    NOT_EVALUABLE = "not_evaluable"


class AssessmentMethod(StrEnum):
    CERTIFIED_VISUAL_GRADING = "certified_visual_grading"
    CERTIFIED_MACHINE_GRADING = "certified_machine_grading"
    APPROVED_NONDESTRUCTIVE_EVALUATION = "approved_nondestructive_evaluation"
    ENGINEERING_ASSESSMENT = "engineering_assessment"
    UNKNOWN = "unknown"


class DefectDataStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class DefectSeverity(StrEnum):
    MINOR = "minor"
    MAJOR = "major"
    UNKNOWN = "unknown"


class ReuseHistoryStatus(StrEnum):
    VERIFIED_RECLAIMED = "verified_reclaimed"
    UNVERIFIED_RECLAIMED = "unverified_reclaimed"
    NEW = "new"
    UNKNOWN = "unknown"


class TreatmentContaminationStatus(StrEnum):
    CLEAR = "clear"
    RESTRICTED = "restricted"
    SUSPECTED = "suspected"
    UNKNOWN = "unknown"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    ALLOCATED = "allocated"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class OrientationRequirement(StrEnum):
    FIXED = "fixed"
    ROTATABLE = "rotatable"
    ANY = "any"


class VisibleSurfaceScope(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class RuleOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUABLE = "not_evaluable"


class RuleScope(StrEnum):
    PAIR = "pair"
    GLOBAL = "global"


class RuleSeverity(StrEnum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"
