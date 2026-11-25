# vNext multi-student evidence scope

Stage 5B keeps the persistent case model explicit: configured students and
stored `SubjectRelationship` records remain the only identity registries.
The Investigator receives deterministic source-applicability categories and
run-local relationship references (`R1`, `R2`, ...). It can propose a
relationship scope only when one admitted source identifies all participants.
The Graph Warden resolves those references, derives a deterministic
case-and-participant relationship ID, and commits the relationship registry
and graph atomically.

Graph scopes remain `CASE`, `SUBJECT`, and `RELATIONSHIP`. Scope compatibility
is directional: case-wide material may narrow to a student or relationship;
private student material cannot widen to another student, a relationship, or
the case. Relationship material may narrow to one of its participants or the
same relationship. Source ancestry is checked without inferring scope from
node prose.

Per-student assessments are independent. Relationship participation is only a
structural evidence scope and never propagates guilt, collaboration, or
knowledge. Public workspace, report, and Help projections expose student
labels and source labels only; run-local refs, canonical IDs, applicability
categories, and graph internals remain backend trace data.

Normal public uploads are admitted as raw sources and cannot supply trusted
assessment scope. Trusted scope metadata is reserved for controlled internal
fixtures and sample setup. No relationship-specific UI or public relationship
creation endpoint is added in this milestone.
