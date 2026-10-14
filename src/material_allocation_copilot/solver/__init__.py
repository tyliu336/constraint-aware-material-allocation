"""Deterministic candidate and bipartite-matching kernel. No AI or persistence."""

from material_allocation_copilot.solver.candidates import build_candidate_matrix
from material_allocation_copilot.solver.feasibility import check_feasibility
from material_allocation_copilot.solver.ranking import rank_weighted_matchings

__all__ = ["build_candidate_matrix", "check_feasibility", "rank_weighted_matchings"]
