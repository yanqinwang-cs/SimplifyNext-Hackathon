# Contract assurance report

- Generated: 2026-09-01T00:00:15.927206+00:00
- Contracts: 7

| Metric | Count |
| --- | ---: |
| Total evaluations | 401 |
| Accepted | 21 |
| Rejected | 380 |
| Unexpected accepts | 0 |
| Unexpected rejects | 0 |
| S5 candidates | 0 |
| S6 limitations | 0 |

- Human review required for S5 candidates: `False`
## Failure codes

- `S0`: 140
- `S1`: 126
- `S2`: 51
- `S3`: 21
- `S4`: 42

## Blind compliance

- Status: `NOT_BLIND`
- Batches: 5
- Qualified batches: 0
- Excluded as NOT_BLIND: 5

| Blind contract | Batches | Qualified | Excluded |
| --- | ---: | ---: | ---: |
| `InitialExpansionResponse` | 2 | 0 | 2 |
| `NextStepResponse` | 1 | 0 | 1 |

| Blind role | Batches | Evaluations | Accepted | Rejected |
| --- | ---: | ---: | ---: | ---: |
| `adversary` | 3 | 1 | 0 | 1 |
| `producer` | 2 | 1 | 0 | 1 |

## Assurance limitations

- S6 reasoning and semantic quality are not assessed by deterministic schema assurance.
- SmokeResponse is schema-sampled offline; its live Bedrock path is excluded from this offline cycle.

## By contract

| Contract | Total | Accepted | Rejected |
| --- | ---: | ---: | ---: |
| `InitialExpansionResponse` | 75 | 2 | 73 |
| `InitialResponse` | 62 | 1 | 61 |
| `ModelScreenHypothesisResponse` | 38 | 2 | 36 |
| `NextActionResponse` | 44 | 1 | 43 |
| `NextStepResponse` | 57 | 3 | 54 |
| `RevisionResponse` | 97 | 11 | 86 |
| `SmokeResponse` | 28 | 1 | 27 |
