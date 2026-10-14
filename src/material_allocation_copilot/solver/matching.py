"""Exact maximum bipartite matching by iterative augmenting paths, plus a weak baseline."""

from collections import deque
from typing import Literal

from material_allocation_copilot.rules import AllocationAssignment
from material_allocation_copilot.solver.models import (
    HallWitness,
    MatchingResult,
    MinimumVertexCover,
)

Graph = dict[str, tuple[str, ...]]


def _canonical(graph: Graph) -> Graph:
    # Partitions use separate dictionaries, so the same string in both is unambiguous.
    return {slot: tuple(sorted(set(neighbors))) for slot, neighbors in sorted(graph.items())}


def _witness(graph: Graph, matched_materials: dict[str, str], root: str) -> HallWitness:
    slots = {root}
    neighbors: set[str] = set()
    queue = deque([root])
    while queue:
        for material in graph[queue.popleft()]:
            if material in neighbors:
                continue
            neighbors.add(material)
            owner = matched_materials.get(material)
            if owner is None:
                raise RuntimeError("Maximum matching certificate contains an augmenting path.")
            if owner not in slots:
                slots.add(owner)
                queue.append(owner)
    # N(S) is enumerated in full. Every neighbor is owned inside S, but root is unmatched.
    return HallWitness(
        slot_ids=tuple(sorted(slots)),
        neighbor_material_ids=tuple(sorted(neighbors)),
        shortage=len(slots) - len(neighbors),
    )


def _minimum_vertex_cover(
    graph: Graph,
    matched_slots: dict[str, str],
    matched_materials: dict[str, str],
) -> MinimumVertexCover:
    """Konig certificate from alternating reachability of every unmatched left vertex."""
    reachable_slots = set(graph) - set(matched_slots)
    reachable_materials: set[str] = set()
    queue = deque(sorted(reachable_slots))
    while queue:
        slot = queue.popleft()
        for material in graph[slot]:
            if matched_slots.get(slot) == material or material in reachable_materials:
                continue
            reachable_materials.add(material)
            owner = matched_materials.get(material)
            if owner is not None and owner not in reachable_slots:
                reachable_slots.add(owner)
                queue.append(owner)
    cover_slots = set(graph) - reachable_slots
    return MinimumVertexCover(
        slot_ids=tuple(sorted(cover_slots)),
        material_ids=tuple(sorted(reachable_materials)),
        size=len(cover_slots) + len(reachable_materials),
    )


def exact_matching(
    graph: Graph, *, scope: Literal["verified", "optimistic"] = "verified"
) -> MatchingResult:
    graph = _canonical(graph)
    matched_slots: dict[str, str] = {}
    matched_materials: dict[str, str] = {}
    for root in graph:
        queue = deque([root])
        seen_slots = {root}
        parent: dict[str, str] = {}
        free_material = None
        while queue and free_material is None:
            slot = queue.popleft()
            for material in graph[slot]:
                if material in parent:
                    continue
                parent[material] = slot
                owner = matched_materials.get(material)
                if owner is None:
                    free_material = material
                    break
                if owner not in seen_slots:
                    seen_slots.add(owner)
                    queue.append(owner)
        if free_material is not None:
            material = free_material
            while True:
                slot = parent[material]
                previous = matched_slots.get(slot)
                matched_slots[slot] = material
                matched_materials[material] = slot
                if previous is None:
                    break
                material = previous
    unmatched = tuple(slot for slot in graph if slot not in matched_slots)
    cover = _minimum_vertex_cover(graph, matched_slots, matched_materials)
    return MatchingResult(
        algorithm="augmenting-path-bfs-v1",
        graph_scope=scope,
        status="no_complete_matching" if unmatched else "complete",
        maximum_cardinality_proven=True,
        required_slot_count=len(graph),
        matched_count=len(matched_slots),
        assignments=tuple(
            AllocationAssignment(slot_id=slot, material_id=material)
            for slot, material in sorted(matched_slots.items())
        ),
        unmatched_slot_ids=unmatched,
        hall_witness=_witness(graph, matched_materials, unmatched[0]) if unmatched else None,
        minimum_vertex_cover=cover,
    )


def greedy_matching(graph: Graph) -> MatchingResult:
    graph = _canonical(graph)
    assignments = []
    used: set[str] = set()
    unmatched = []
    for slot, neighbors in graph.items():
        material = next((item for item in neighbors if item not in used), None)
        if material is None:
            unmatched.append(slot)
        else:
            used.add(material)
            assignments.append(AllocationAssignment(slot_id=slot, material_id=material))
    return MatchingResult(
        algorithm="greedy-id-order-v1",
        graph_scope="verified",
        status="method_no_complete_solution" if unmatched else "complete",
        maximum_cardinality_proven=False,
        required_slot_count=len(graph),
        matched_count=len(assignments),
        assignments=tuple(assignments),
        unmatched_slot_ids=tuple(unmatched),
    )
