# Qualitative model screen

This is a small, manually reviewed screen of one model at a time against five synthetic case contexts. It records structured hypotheses and call metadata; it does not score outputs, infer hidden ground truth, or implement investigative reasoning.

Configure AWS credentials and `BEDROCK_MODEL_ID` in the environment or a local `.env` file, then run:

```bash
uv run python experiments/model_screen/run.py --model <BEDROCK_MODEL_OR_PROFILE_ID>
uv run python experiments/model_screen/run.py --model <BEDROCK_MODEL_OR_PROFILE_ID> --case case_01
```

The runner makes one call per selected case, continues after case-level failures, and writes each run to a new timestamped directory under `results/`.

