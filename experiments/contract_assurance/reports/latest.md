# Contract assurance report

- Generated: 2026-09-01T01:21:24.167622+00:00
- Contracts: 7

| Metric | Count |
| --- | ---: |
| Total evaluations | 428 |
| Accepted | 21 |
| Rejected | 407 |
| Unexpected accepts | 0 |
| Unexpected rejects | 0 |
| S5 candidates | 0 |
| S6 limitations | 0 |

- Human review required for S5 candidates: `False`
- Observed deterministic failure rate: `0.9509`; upper 95% bound: `0.9677` (compliance statistic, not reasoning confidence).
- Valid-fixture pass rate: `1.0000`; invalid-fixture rejection rate: `1.0000`.
## Failure codes

- `S0`: 140
- `S1`: 112
- `S2`: 56
- `S3`: 22
- `S4`: 77

## Production-path stages

- `availability`: 3
- `operation_preflight`: 18
- `schema`: 258
- `serialization`: 140
- `state_operation_preflight`: 9

## Changes made

- Deterministic fixtures evaluated through registered production-path adapters.
- Qualified blind manifests and output metrics aggregated without admitting NOT_BLIND results.

## Regressions

- `status`: clean
- `unexpected_accepts`: 0
- `unexpected_rejects`: 0

## Remaining risks

- S6 reasoning and semantic quality require a separate semantic checker.
- SmokeResponse live Bedrock execution remains excluded by the no-AWS constraint.
- Historical NOT_BLIND batches remain excluded from blind compliance statistics.

## Blind compliance

- Status: `NOT_BLIND`
- Batches: 21
- Qualified batches: 14
- Excluded as NOT_BLIND: 7

| Blind contract | Batches | Qualified | Excluded |
| --- | ---: | ---: | ---: |
| `InitialExpansionResponse` | 6 | 2 | 4 |
| `InitialResponse` | 2 | 2 | 0 |
| `ModelScreenHypothesisResponse` | 2 | 2 | 0 |
| `NextActionResponse` | 2 | 2 | 0 |
| `NextStepResponse` | 3 | 2 | 1 |
| `RevisionResponse` | 2 | 2 | 0 |
| `SmokeResponse` | 4 | 2 | 2 |

| Blind role | Batches | Qualified evaluations | Accepted | Rejected |
| --- | ---: | ---: | ---: | ---: |
| `adversary` | 12 | 7 | 0 | 7 |
| `producer` | 11 | 7 | 7 | 0 |
- Qualified blind failure codes: `{'S0': 1, 'S1': 1, 'S3': 2, 'S4': 3}`
- Qualified blind output metrics: placeholder copies `1`, fenced outputs `0`, average length `366.6` characters.

## Assurance limitations

- S6 reasoning and semantic quality are not assessed by deterministic schema assurance.
- SmokeResponse is schema-sampled offline; its live Bedrock path is excluded from this offline cycle.

## By contract

| Contract | Total | Accepted | Rejected |
| --- | ---: | ---: | ---: |
| `InitialExpansionResponse` | 87 | 2 | 85 |
| `InitialResponse` | 65 | 1 | 64 |
| `ModelScreenHypothesisResponse` | 38 | 2 | 36 |
| `NextActionResponse` | 44 | 1 | 43 |
| `NextStepResponse` | 58 | 3 | 55 |
| `RevisionResponse` | 108 | 11 | 97 |
| `SmokeResponse` | 28 | 1 | 27 |
