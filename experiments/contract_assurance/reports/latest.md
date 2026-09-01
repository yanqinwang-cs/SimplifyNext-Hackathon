# Contract assurance report

- Generated: 2026-09-01T00:22:49.461216+00:00
- Contracts: 7

| Metric | Count |
| --- | ---: |
| Total evaluations | 411 |
| Accepted | 21 |
| Rejected | 390 |
| Unexpected accepts | 0 |
| Unexpected rejects | 0 |
| S5 candidates | 0 |
| S6 limitations | 0 |

- Human review required for S5 candidates: `False`
## Failure codes

- `S0`: 140
- `S1`: 112
- `S2`: 56
- `S3`: 21
- `S4`: 61

## Blind compliance

- Status: `NOT_BLIND`
- Batches: 19
- Qualified batches: 14
- Excluded as NOT_BLIND: 5

| Blind contract | Batches | Qualified | Excluded |
| --- | ---: | ---: | ---: |
| `InitialExpansionResponse` | 4 | 2 | 2 |
| `InitialResponse` | 2 | 2 | 0 |
| `ModelScreenHypothesisResponse` | 2 | 2 | 0 |
| `NextActionResponse` | 2 | 2 | 0 |
| `NextStepResponse` | 3 | 2 | 1 |
| `RevisionResponse` | 2 | 2 | 0 |
| `SmokeResponse` | 4 | 2 | 2 |

| Blind role | Batches | Evaluations | Accepted | Rejected |
| --- | ---: | ---: | ---: | ---: |
| `adversary` | 11 | 9 | 1 | 8 |
| `producer` | 10 | 9 | 8 | 1 |

## Assurance limitations

- S6 reasoning and semantic quality are not assessed by deterministic schema assurance.
- SmokeResponse is schema-sampled offline; its live Bedrock path is excluded from this offline cycle.

## By contract

| Contract | Total | Accepted | Rejected |
| --- | ---: | ---: | ---: |
| `InitialExpansionResponse` | 82 | 2 | 80 |
| `InitialResponse` | 62 | 1 | 61 |
| `ModelScreenHypothesisResponse` | 38 | 2 | 36 |
| `NextActionResponse` | 44 | 1 | 43 |
| `NextStepResponse` | 58 | 3 | 55 |
| `RevisionResponse` | 99 | 11 | 88 |
| `SmokeResponse` | 28 | 1 | 27 |
