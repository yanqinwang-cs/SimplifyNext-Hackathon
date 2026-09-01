# Contract assurance report

- Generated: 2026-09-01T01:52:06.393708+00:00
- Contracts: 7

| Metric | Count |
| --- | ---: |
| Total evaluations | 431 |
| Accepted | 21 |
| Rejected | 410 |
| Unexpected accepts | 0 |
| Unexpected rejects | 0 |
| S5 candidates | 0 |
| S6 limitations | 0 |

- Human review required for S5 candidates: `False`
- Observed deterministic failure rate: `0.9513`; upper 95% bound: `0.9679` (compliance statistic, not reasoning confidence).
- Valid-fixture pass rate: `1.0000`; invalid-fixture rejection rate: `1.0000`.
## Failure codes

- `S0`: 140
- `S1`: 112
- `S2`: 59
- `S3`: 22
- `S4`: 77

## Production-path stages

- `availability`: 3
- `operation_preflight`: 18
- `schema`: 261
- `serialization`: 140
- `state_operation_preflight`: 9

## Prompt/schema/template lint

- Status: `clean`
- Issues: 0

## Changes made

- Deterministic fixtures evaluated through registered production-path adapters.
- Registered prompt/schema/template lint runs as a deterministic gate.
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
| `InitialExpansionResponse` | 88 | 2 | 86 |
| `InitialResponse` | 66 | 1 | 65 |
| `ModelScreenHypothesisResponse` | 38 | 2 | 36 |
| `NextActionResponse` | 44 | 1 | 43 |
| `NextStepResponse` | 58 | 3 | 55 |
| `RevisionResponse` | 109 | 11 | 98 |
| `SmokeResponse` | 28 | 1 | 27 |

## Contract provenance

| Contract | Production path | Schema hash | Prompt hash(es) | Template hash |
| --- | --- | --- | --- | --- |
| `InitialExpansionResponse` | InvestigationService.start_case(seed) -> environment.build_seeded_initial_state | `3ab84fb21b95fbb73d063a580acd3f51ec5299a5657a7a6ea62d6b61e696b7c2` | src/investigator/environments/case_01_prompts.py: db8d5fb1b0f793ac73f616331e3bf27737803e78360af972ab68431455507dce | `7dfaac579095ea06e47e6f90acf99ce4aa5703d41cc5961941410a94ce1327c7` |
| `InitialResponse` | InvestigationService.start_case -> environment.build_initial_state | `0bf2d70051b0216d9f2cefa9432cf13b87ad214e2e0e13d710686fa4103978a2` | src/investigator/environments/case_01_prompts.py: db8d5fb1b0f793ac73f616331e3bf27737803e78360af972ab68431455507dce | `1074a99932e73cc8a356d8c73c93ca3717757e1ebf86930076f5d355a5143a91` |
| `ModelScreenHypothesisResponse` | ExperimentRunner.run -> ModelClient.call | `f2224a7d45a3c36db7990ef4095c4ba27da435a44a6b8a3fbbe1c5cd0bf54cdc` | experiments/model_screen/prompt.py: bef58f26e2983f7740ec4f450c74685d27a54750ccb58a5ebb96027affa9c0ce | `b8966bcd51627de2fb956b7b51d633622d371faab42dbaf1492617b90f09add4` |
| `NextActionResponse` | InvestigationService.propose_next_action -> availability preflight | `2ee01b9e0a44aa5ea0159022f89f32b407b9f1bdd33c88f8914b62244611a300` | src/investigator/environments/case_01_prompts.py: db8d5fb1b0f793ac73f616331e3bf27737803e78360af972ab68431455507dce | `a26dd84ceed8aed26a8ba78d1e4c6a5d33d192a1051c777285ab09bbfc33cec5` |
| `NextStepResponse` | Defined LLM-facing union; no current production caller | `f0186ca2f50494c9a630a22fe3bdc890a942cb2607bbe1d15d23957dcf668f29` | none | `2d2dd7902ecb2f042fbf318a8a98b11ba75c1125586fcc024c80373eae176113` |
| `RevisionResponse` | InvestigationService.propose_revision -> apply_revision | `56906e23079db421c3704a1465b15ff9c008cbde48b2b4d5776f4c4c1e59a455` | src/investigator/environments/case_01_prompts.py: db8d5fb1b0f793ac73f616331e3bf27737803e78360af972ab68431455507dce | `2ef32d075192816a26ddba52189c1efdcbadc4ba7a6f9aeeeba09b87866fafc6` |
| `SmokeResponse` | BedrockModelClient.call (manual smoke script; excluded from offline runs) | `fc008e3ce54e1de048758174b829e39bd4c47b13edbd2574f60044ecce9e9f22` | scripts/smoke_bedrock.py: 5248e4bdee0d861bb8e5440e8fafc5708749bd084f88c5b67b274a336bfaa89b | `5575ae1c4f02063d00c02afe7bf4137684215f5dabb64eefebb44f98e90ff156` |
