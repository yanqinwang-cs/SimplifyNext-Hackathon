# Contract assurance report

- Generated: 2026-08-31T21:44:26.972002+00:00
- Contracts: 7

| Metric | Count |
| --- | ---: |
| Total evaluations | 370 |
| Accepted | 21 |
| Rejected | 349 |
| Unexpected accepts | 0 |
| Unexpected rejects | 0 |
| S5 candidates | 0 |
| S6 limitations | 0 |

## Failure codes

- `S0`: 140
- `S1`: 126
- `S2`: 43
- `S3`: 17
- `S4`: 23

## Blind compliance

- Status: `NOT_BLIND`
- Batches: 3
- Qualified batches: 0
- Excluded as NOT_BLIND: 3

## Assurance limitations

- S6 reasoning and semantic quality are not assessed by deterministic schema assurance.
- SmokeResponse is schema-sampled offline; its live Bedrock path is excluded from this offline cycle.

## By contract

| Contract | Total | Accepted | Rejected |
| --- | ---: | ---: | ---: |
| `InitialExpansionResponse` | 71 | 2 | 69 |
| `InitialResponse` | 56 | 1 | 55 |
| `ModelScreenHypothesisResponse` | 32 | 2 | 30 |
| `NextActionResponse` | 44 | 1 | 43 |
| `NextStepResponse` | 57 | 3 | 54 |
| `RevisionResponse` | 83 | 11 | 72 |
| `SmokeResponse` | 27 | 1 | 26 |
