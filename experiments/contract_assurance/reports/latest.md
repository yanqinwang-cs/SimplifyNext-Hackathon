# Contract assurance report

- Generated: 2026-09-01T00:15:30.701081+00:00
- Contracts: 7

| Metric | Count |
| --- | ---: |
| Total evaluations | 409 |
| Accepted | 21 |
| Rejected | 388 |
| Unexpected accepts | 0 |
| Unexpected rejects | 0 |
| S5 candidates | 0 |
| S6 limitations | 0 |

- Human review required for S5 candidates: `False`
## Failure codes

- `S0`: 140
- `S1`: 127
- `S2`: 56
- `S3`: 21
- `S4`: 44

## Blind compliance

- Status: `NOT_BLIND`
- Batches: 11
- Qualified batches: 6
- Excluded as NOT_BLIND: 5

| Blind contract | Batches | Qualified | Excluded |
| --- | ---: | ---: | ---: |
| `InitialExpansionResponse` | 2 | 0 | 2 |
| `InitialResponse` | 2 | 2 | 0 |
| `NextActionResponse` | 2 | 2 | 0 |
| `NextStepResponse` | 3 | 2 | 1 |
| `SmokeResponse` | 2 | 0 | 2 |

| Blind role | Batches | Evaluations | Accepted | Rejected |
| --- | ---: | ---: | ---: | ---: |
| `adversary` | 7 | 5 | 1 | 4 |
| `producer` | 6 | 5 | 4 | 1 |

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
| `RevisionResponse` | 97 | 11 | 86 |
| `SmokeResponse` | 28 | 1 | 27 |
