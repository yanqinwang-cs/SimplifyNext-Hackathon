# Controlled 10-candidate scale fixture

This folder holds the evaluator-only validation material for the ten-candidate
scale fixture. It combines the revised Case 5A pattern for Candidates A–E with a
relabelled Case 5B pattern for Candidates F–J. The public portion (manifest and
sources) lives in `src/investigator/public_samples/multi_candidate_10` and is
served as the `multi-candidate-10` sample; the material here is never loaded
into a case.

To load it as an ordinary developer case without running an assessment:

```bash
uv run python scripts/load_scale_fixture.py \\
  src/investigator/public_samples/multi_candidate_10 \\
  --repository data/cases \\
  --case-id scale-10-working
```
