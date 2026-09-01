# Public contract snapshots

The frozen JSON files in [`../blind/packages/`](../blind/packages/) are the
committed public contract snapshots. Each package contains the prompt, case
input, output schema, template, and hashes needed to verify provenance without
exposing implementation or adversarial results.

The `blind/packages/` location is intentional: the same immutable package is
the input boundary for isolated producer and adversary workers. Use
`write_public_snapshot` when regenerating a package, then run the assurance
runner to verify every registered contract before committing it.
