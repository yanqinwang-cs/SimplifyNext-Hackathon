"""Authoritative semantic graph operation type specifications."""

from dataclasses import dataclass
from itertools import product

from investigator.graph.models import EdgeRelation, GraphNodeType


@dataclass(frozen=True)
class OperationSpec:
    relation: EdgeRelation
    allowed_pairs: frozenset[tuple[GraphNodeType, GraphNodeType]]

    def allows(self, source: GraphNodeType, target: GraphNodeType) -> bool:
        return (source, target) in self.allowed_pairs


_E, _P, _H, _U = GraphNodeType.EVIDENCE, GraphNodeType.PROPOSITION, GraphNodeType.HYPOTHESIS, GraphNodeType.UNCERTAINTY

OPERATION_SPECS = {
    EdgeRelation.DERIVED_FROM: OperationSpec(EdgeRelation.DERIVED_FROM, frozenset(product((_P,), (_E, _P)))),
    EdgeRelation.SUPPORTS: OperationSpec(EdgeRelation.SUPPORTS, frozenset(product((_E, _P), (_P, _H)))),
    EdgeRelation.CONFLICTS: OperationSpec(EdgeRelation.CONFLICTS, frozenset(product((_E, _P), (_P, _H)))),
    EdgeRelation.TARGETS: OperationSpec(EdgeRelation.TARGETS, frozenset(product((_U,), (_E, _P, _H)))),
    EdgeRelation.SPECIALIZES: OperationSpec(EdgeRelation.SPECIALIZES, frozenset(product((_H,), (_H,)))),
    EdgeRelation.DEPENDS_ON: OperationSpec(EdgeRelation.DEPENDS_ON, frozenset(product((_H, _P), (_P, _H)))),
}


class OperationSpecRegistry:
    """Single source for the complete binary semantic-operation whitelist."""

    @classmethod
    def get(cls, relation: EdgeRelation) -> OperationSpec:
        return OPERATION_SPECS[relation]

    @classmethod
    def allows(cls, relation: EdgeRelation, source: GraphNodeType, target: GraphNodeType) -> bool:
        return cls.get(relation).allows(source, target)

    @classmethod
    def matrix(cls, relation: EdgeRelation) -> dict[tuple[GraphNodeType, GraphNodeType], bool]:
        semantic_types = (GraphNodeType.EVIDENCE, GraphNodeType.PROPOSITION, GraphNodeType.HYPOTHESIS, GraphNodeType.UNCERTAINTY)
        return {(source, target): cls.allows(relation, source, target) for source, target in product(semantic_types, repeat=2)}
