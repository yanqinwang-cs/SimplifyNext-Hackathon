# Contract assurance report

- Generated: 2026-09-01T02:42:57.731473+00:00
- Contracts: 8

| Metric | Count |
| --- | ---: |
| Total evaluations | 477 |
| Accepted | 34 |
| Rejected | 443 |
| Unexpected accepts | 0 |
| Unexpected rejects | 0 |
| S5 candidates | 0 |
| S6 limitations | 0 |

- Human review required for S5 candidates: `False`
- Observed deterministic failure rate: `0.9287`; upper 95% bound: `0.9485` (compliance statistic, not reasoning confidence).
- Valid-fixture pass rate: `1.0000`; invalid-fixture rejection rate: `1.0000`.
## Failure codes

- `S0`: 156
- `S1`: 125
- `S2`: 59
- `S3`: 22
- `S4`: 81

## Production-path stages

- `availability`: 3
- `coordinator`: 13
- `coordinator_preflight`: 4
- `operation_preflight`: 18
- `schema`: 274
- `serialization`: 156
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
- Batches: 31
- Qualified batches: 24
- Excluded as NOT_BLIND: 7

| Blind contract | Batches | Qualified | Excluded |
| --- | ---: | ---: | ---: |
| `InitialExpansionResponse` | 6 | 2 | 4 |
| `InitialResponse` | 2 | 2 | 0 |
| `ModelScreenHypothesisResponse` | 4 | 4 | 0 |
| `NextActionResponse` | 6 | 6 | 0 |
| `NextStepResponse` | 3 | 2 | 1 |
| `RevisionResponse` | 2 | 2 | 0 |
| `SmokeResponse` | 4 | 2 | 2 |
| `StewardDecisionResponse` | 4 | 4 | 0 |

| Blind contract/role | Batches | Qualified | Excluded | Evaluations | Accepted | Rejected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `InitialExpansionResponse` / `adversary` | 4 | 1 | 3 | 1 | 0 | 1 |
| `InitialExpansionResponse` / `producer` | 3 | 1 | 2 | 1 | 1 | 0 |
| `InitialResponse` / `adversary` | 1 | 1 | 0 | 1 | 0 | 1 |
| `InitialResponse` / `producer` | 1 | 1 | 0 | 1 | 1 | 0 |
| `ModelScreenHypothesisResponse` / `adversary` | 2 | 2 | 0 | 2 | 1 | 1 |
| `ModelScreenHypothesisResponse` / `producer` | 2 | 2 | 0 | 2 | 2 | 0 |
| `NextActionResponse` / `adversary` | 3 | 3 | 0 | 3 | 1 | 2 |
| `NextActionResponse` / `producer` | 3 | 3 | 0 | 3 | 3 | 0 |
| `NextStepResponse` / `adversary` | 2 | 1 | 1 | 1 | 0 | 1 |
| `NextStepResponse` / `producer` | 2 | 1 | 1 | 1 | 1 | 0 |
| `RevisionResponse` / `adversary` | 1 | 1 | 0 | 1 | 0 | 1 |
| `RevisionResponse` / `producer` | 1 | 1 | 0 | 1 | 1 | 0 |
| `SmokeResponse` / `adversary` | 2 | 1 | 1 | 1 | 0 | 1 |
| `SmokeResponse` / `producer` | 2 | 1 | 1 | 1 | 1 | 0 |
| `StewardDecisionResponse` / `adversary` | 2 | 2 | 0 | 2 | 0 | 2 |
| `StewardDecisionResponse` / `producer` | 2 | 2 | 0 | 2 | 2 | 0 |

| Blind role | Batches | Qualified evaluations | Accepted | Rejected |
| --- | ---: | ---: | ---: | ---: |
| `adversary` | 17 | 12 | 2 | 10 |
| `producer` | 16 | 12 | 12 | 0 |
- Qualified blind failure codes: `{'S0': 1, 'S1': 2, 'S3': 3, 'S4': 4}`
- Qualified blind output metrics: placeholder copies `1`, fenced outputs `0`, average length `377.6` characters.

## Assurance limitations

- S6 reasoning and semantic quality are not assessed by deterministic schema assurance.
- SmokeResponse is schema-sampled offline; its live Bedrock path is excluded from this offline cycle.

## By contract

| Contract | Total | Accepted | Rejected | Valid pass | Invalid reject | S0 | S1 | S2 | S3 | S4 | S5 | S6 | Unexpected accepts | Unexpected rejects |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `InitialExpansionResponse` | 88 | 2 | 86 | 1.0000 | 1.0000 | 20 | 26 | 20 | 4 | 16 | 0 | 0 | 0 | 0 |
| `InitialResponse` | 66 | 1 | 65 | 1.0000 | 1.0000 | 20 | 19 | 12 | 3 | 11 | 0 | 0 | 0 | 0 |
| `ModelScreenHypothesisResponse` | 38 | 2 | 36 | 1.0000 | 1.0000 | 20 | 10 | 0 | 0 | 6 | 0 | 0 | 0 | 0 |
| `NextActionResponse` | 44 | 1 | 43 | 1.0000 | 1.0000 | 20 | 12 | 7 | 1 | 3 | 0 | 0 | 0 | 0 |
| `NextStepResponse` | 58 | 3 | 55 | 1.0000 | 1.0000 | 20 | 14 | 10 | 0 | 11 | 0 | 0 | 0 | 0 |
| `RevisionResponse` | 109 | 11 | 98 | 1.0000 | 1.0000 | 20 | 26 | 10 | 14 | 28 | 0 | 0 | 0 | 0 |
| `SmokeResponse` | 28 | 1 | 27 | 1.0000 | 1.0000 | 20 | 5 | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| `StewardDecisionResponse` | 46 | 13 | 33 | 1.0000 | 1.0000 | 16 | 13 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |

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
| `StewardDecisionResponse` | steward_screen.runner.run_live -> GraphInvestigationCoordinator.review_with_steward | `c1d034291a6ea54ed9e0805c81e02ec4d6c81044e24d58acfa80d0fb6492e705` | experiments/steward_screen/prompt.py: 0b29e5c739a512ba0641d96050c363250c0d3da700940f22d59aaa62f72a0cd6 | `c90166b5084d9b7c6d64eaf3522e79f3ae8101fd93a5aa17636d45e8d7d54cd7` |
