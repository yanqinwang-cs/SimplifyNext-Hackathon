# Controlled 15-candidate scale fixture

This is evaluator-only validation material. It combines three disjoint cohorts:
revised Case 5A (A–E), relabelled revised Case 5B (F–J), and relabelled revised
Case 5C (K–O). It is not a public sample and is never added to the sample
catalog.

To load it as an ordinary developer case without running an assessment:

```bash
uv run python scripts/load_scale_fixture.py \\
  tests/fixtures/vnext_scale/case_15_5a_plus_5b_plus_5c \\
  --repository data/cases \\
  --case-id scale-15-working
```

Expected benchmark roles are evaluator guidance only and MUST NOT enter
Investigator-visible inputs: A/B strongest collaboration; F/G and K/L weaker
comparisons; E/J/O device positives; C/D/H/I/M/N predominantly negative
controls. Every candidate is assessed independently: 15 candidates × 4
violations = 60 findings.
