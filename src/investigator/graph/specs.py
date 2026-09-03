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


@dataclass(frozen=True)
class ReferenceSpec:
    field: str
    allowed_types: frozenset[GraphNodeType]
    itemized: bool = False


@dataclass(frozen=True)
class OperationContract:
    operation: str
    created_type: GraphNodeType | None
    references: tuple[ReferenceSpec, ...] = ()

    def reference(self, field: str) -> ReferenceSpec:
        for item in self.references:
            if item.field == field:
                return item
        raise KeyError(f"No reference contract for {self.operation}.{field}")


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

    @classmethod
    def contract(cls, operation: str) -> OperationContract:
        return OPERATION_CONTRACTS[operation]

    @classmethod
    def allowed_types_for(cls, operation: str, field: str) -> frozenset[GraphNodeType]:
        return cls.contract(operation).reference(field).allowed_types


_OPERATION_TYPES = {
    "evidence": GraphNodeType.EVIDENCE,
    "proposition": GraphNodeType.PROPOSITION,
    "hypothesis": GraphNodeType.HYPOTHESIS,
    "uncertainty": GraphNodeType.UNCERTAINTY,
}


OPERATION_CONTRACTS = {
    "add_evidence": OperationContract("add_evidence", _OPERATION_TYPES["evidence"]),
    "add_proposition": OperationContract(
        "add_proposition", _OPERATION_TYPES["proposition"],
        (ReferenceSpec("derived_from_node_ids", frozenset({_OPERATION_TYPES["evidence"], _OPERATION_TYPES["proposition"]}), True),),
    ),
    "add_hypothesis": OperationContract("add_hypothesis", _OPERATION_TYPES["hypothesis"]),
    "add_uncertainty": OperationContract(
        "add_uncertainty", _OPERATION_TYPES["uncertainty"],
        (ReferenceSpec("target_node_id", frozenset({_OPERATION_TYPES["evidence"], _OPERATION_TYPES["proposition"], _OPERATION_TYPES["hypothesis"]})),),
    ),
    "add_support": OperationContract(
        "add_support", None,
        (ReferenceSpec("source_node_id", frozenset({_OPERATION_TYPES["evidence"], _OPERATION_TYPES["proposition"]})),
         ReferenceSpec("target_node_id", frozenset({_OPERATION_TYPES["proposition"], _OPERATION_TYPES["hypothesis"]}))),
    ),
    "add_conflict": OperationContract(
        "add_conflict", None,
        (ReferenceSpec("source_node_id", frozenset({_OPERATION_TYPES["evidence"], _OPERATION_TYPES["proposition"]})),
         ReferenceSpec("target_node_id", frozenset({_OPERATION_TYPES["proposition"], _OPERATION_TYPES["hypothesis"]}))),
    ),
    "add_derivation": OperationContract(
        "add_derivation", None,
        (ReferenceSpec("derived_proposition_id", frozenset({_OPERATION_TYPES["proposition"]})),
         ReferenceSpec("source_node_id", frozenset({_OPERATION_TYPES["evidence"], _OPERATION_TYPES["proposition"]}))),
    ),
    "add_specialization": OperationContract(
        "add_specialization", None,
        (ReferenceSpec("child_hypothesis_id", frozenset({_OPERATION_TYPES["hypothesis"]})),
         ReferenceSpec("parent_hypothesis_id", frozenset({_OPERATION_TYPES["hypothesis"]}))),
    ),
}
