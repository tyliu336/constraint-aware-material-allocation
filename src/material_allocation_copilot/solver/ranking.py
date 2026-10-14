"""Exact weighted complete matching and deterministic top-k partition search."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from decimal import Decimal

from material_allocation_copilot.rules import AllocationAssignment

EdgeKey = tuple[str, str]
WeightedGraph = dict[str, dict[str, Decimal]]


@dataclass(frozen=True, slots=True)
class WeightedSolution:
    assignments: tuple[AllocationAssignment, ...]
    total_score: Decimal

    @property
    def identity(self) -> tuple[EdgeKey, ...]:
        return tuple((item.slot_id, item.material_id) for item in self.assignments)


@dataclass(frozen=True, slots=True)
class RankingSearchResult:
    solutions: tuple[WeightedSolution, ...]
    status: str
    subproblems_solved: int


@dataclass(frozen=True, slots=True)
class _Subproblem:
    forced: tuple[EdgeKey, ...] = ()
    excluded: frozenset[EdgeKey] = frozenset()


@dataclass(slots=True)
class _FlowEdge:
    to: int
    reverse: int
    capacity: int
    cost: Decimal


def _add_edge(network: list[list[_FlowEdge]], source: int, target: int, cost: Decimal) -> int:
    forward_index = len(network[source])
    reverse_index = len(network[target])
    network[source].append(_FlowEdge(target, reverse_index, 1, cost))
    network[target].append(_FlowEdge(source, forward_index, 0, -cost))
    return forward_index


def _best_constrained(graph: WeightedGraph, subproblem: _Subproblem) -> WeightedSolution | None:
    slots = tuple(sorted(graph))
    forced = dict(subproblem.forced)
    if len(forced) != len(subproblem.forced):
        return None
    forced_materials = tuple(forced.values())
    if len(forced_materials) != len(set(forced_materials)):
        return None
    for slot_id, material_id in subproblem.forced:
        if (
            material_id not in graph.get(slot_id, {})
            or (slot_id, material_id) in subproblem.excluded
        ):
            return None

    remaining_slots = tuple(slot_id for slot_id in slots if slot_id not in forced)
    blocked_materials = set(forced_materials)
    materials = tuple(
        sorted(
            {
                material_id
                for slot_id in remaining_slots
                for material_id in graph[slot_id]
                if material_id not in blocked_materials
                and (slot_id, material_id) not in subproblem.excluded
            }
        )
    )
    if len(materials) < len(remaining_slots):
        return None
    source = 0
    slot_offset = 1
    material_offset = slot_offset + len(remaining_slots)
    sink = material_offset + len(materials)
    network: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]
    material_index = {
        material_id: material_offset + index for index, material_id in enumerate(materials)
    }
    assignment_edges: dict[EdgeKey, tuple[int, int]] = {}
    for index, slot_id in enumerate(remaining_slots):
        slot_node = slot_offset + index
        _add_edge(network, source, slot_node, Decimal(0))
        for material_id, score in sorted(graph[slot_id].items()):
            if material_id in blocked_materials or (slot_id, material_id) in subproblem.excluded:
                continue
            edge_index = _add_edge(
                network, slot_node, material_index[material_id], Decimal(1) - score
            )
            assignment_edges[(slot_id, material_id)] = (slot_node, edge_index)
    for material_id in materials:
        _add_edge(network, material_index[material_id], sink, Decimal(0))

    potentials = [Decimal(0)] * len(network)
    for _ in remaining_slots:
        distances: list[Decimal | None] = [None] * len(network)
        parents: list[tuple[int, int] | None] = [None] * len(network)
        distances[source] = Decimal(0)
        queue: list[tuple[Decimal, int]] = [(Decimal(0), source)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distances[node] != distance:
                continue
            for edge_index, edge in enumerate(network[node]):
                if edge.capacity == 0:
                    continue
                reduced = edge.cost + potentials[node] - potentials[edge.to]
                candidate = distance + reduced
                if distances[edge.to] is None or candidate < distances[edge.to]:
                    distances[edge.to] = candidate
                    parents[edge.to] = (node, edge_index)
                    heapq.heappush(queue, (candidate, edge.to))
        if distances[sink] is None:
            return None
        for node, distance in enumerate(distances):
            if distance is not None:
                potentials[node] += distance
        node = sink
        while node != source:
            parent = parents[node]
            if parent is None:
                raise RuntimeError("weighted matching path is incomplete")
            previous, edge_index = parent
            edge = network[previous][edge_index]
            edge.capacity = 0
            network[node][edge.reverse].capacity = 1
            node = previous

    assignments = list(
        AllocationAssignment(slot_id=slot, material_id=material)
        for slot, material in subproblem.forced
    )
    for (slot_id, material_id), (node, edge_index) in assignment_edges.items():
        if network[node][edge_index].capacity == 0:
            assignments.append(AllocationAssignment(slot_id=slot_id, material_id=material_id))
    assignments.sort(key=lambda item: item.slot_id)
    if len(assignments) != len(slots):
        raise RuntimeError("weighted matching did not cover every slot")
    total = sum((graph[item.slot_id][item.material_id] for item in assignments), Decimal(0))
    return WeightedSolution(assignments=tuple(assignments), total_score=total)


def rank_weighted_matchings(
    graph: WeightedGraph,
    *,
    limit: int,
    subproblem_budget: int = 250,
) -> RankingSearchResult:
    if limit < 1 or subproblem_budget < 1:
        raise ValueError("ranking limits must be positive")
    root = _Subproblem()
    first = _best_constrained(graph, root)
    solved = 1
    if first is None:
        return RankingSearchResult((), "infeasible", solved)
    sequence = 0
    heap: list[tuple[Decimal, tuple[EdgeKey, ...], int, _Subproblem, WeightedSolution]] = []
    heapq.heappush(heap, (-first.total_score, first.identity, sequence, root, first))
    output: list[WeightedSolution] = []
    while heap and len(output) < limit:
        _, _, _, problem, solution = heapq.heappop(heap)
        output.append(solution)
        if len(output) == limit:
            return RankingSearchResult(tuple(output), "exact_top_k", solved)
        fixed = dict(problem.forced)
        free_slots = tuple(slot for slot in sorted(graph) if slot not in fixed)
        if solved + len(free_slots) > subproblem_budget:
            return RankingSearchResult(tuple(output), "budget_exhausted", solved)
        chosen = dict(solution.identity)
        for slot_id in free_slots:
            edge = (slot_id, chosen[slot_id])
            child = _Subproblem(
                forced=tuple(sorted(fixed.items())),
                excluded=problem.excluded | {edge},
            )
            child_solution = _best_constrained(graph, child)
            solved += 1
            if child_solution is not None:
                sequence += 1
                heapq.heappush(
                    heap,
                    (
                        -child_solution.total_score,
                        child_solution.identity,
                        sequence,
                        child,
                        child_solution,
                    ),
                )
            fixed[slot_id] = chosen[slot_id]
    return RankingSearchResult(tuple(output), "all_feasible_plans_enumerated", solved)
