# Controlled 10-candidate scale fixture

This is evaluator-only validation material. It combines the revised Case 5A
pattern for Candidates A–E with a relabelled Case 5B pattern for Candidates
F–J. It is not a public sample and is never added to the sample catalog.

To load it as an ordinary developer case without running an assessment:

```bash
uv run python scripts/load_scale_fixture.py \\
  tests/fixtures/vnext_scale/case_10_5a_plus_5b \\
  --repository data/cases \\
  --case-id scale-10-working
```
