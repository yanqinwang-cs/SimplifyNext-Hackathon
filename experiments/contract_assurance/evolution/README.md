# Schema-evolution records

Create one immutable record per proposed or implemented contract change. Use a stable change ID and include: contract, discovery source, baseline raw output, S0–S6 classification, legitimate semantic need, why the existing contract was insufficient, exact proposed semantics, invariants, downstream operation behavior, fixtures added, expected reclassifications, unexpected regressions, before/after compliance, and commit.

S5 candidates require human semantic approval before implementation. A model inventing a value is not sufficient evidence for an extension. S6 findings remain outside deterministic schema assurance.
