"""Developer-only real-model smoke test for the finite vNext pipeline.

Run manually with the normal AWS credential chain, for example::

    VNEXT_INVESTIGATOR_MODEL_ID=<model-id> AWS_PROFILE=<profile> \
        uv run python -m investigator.vnext.smoke

This module performs one Bedrock call, with no retry, Workspace, Steward, or
evidence-request flow. It does not mutate a persistent case.
"""

import argparse
import json
import os
import sys

from typing import Any

from investigator.llm import BedrockModelClient, ModelCallMetadata, ModelClient
from investigator.vnext import (
    AssessmentRulePreset,
    VNextInvestigationRunner,
    VNextRunInput,
    VNextRunResult,
    ViolationDefinition,
)
from investigator.vnext.model import VNextInvestigatorModel, build_prompt


def smoke_rule_preset() -> AssessmentRulePreset:
    return AssessmentRulePreset(
        preset_id="vnext-smoke-device-rule",
        violations=[
            ViolationDefinition(
                violation_id="unauthorized_device",
                label="Unauthorized electronic device",
                rule_text="Unauthorized electronic devices are prohibited during the assessment.",
                prohibited_conduct="Possessing an unauthorized electronic device during the assessment.",
            )
        ],
    )


def smoke_run_input(case: str = "obvious") -> VNextRunInput:
    """Return a clean, in-memory smoke fixture without persistent graph state."""
    from investigator.models.source import Source, SourceType

    if case == "obvious":
        sources = {
            "S1": Source(
                id="S1",
                name="Invigilator observation",
                source_type=SourceType.PERSON,
                content="The invigilator observed the student wearing smart eyewear during the assessment.",
            ),
            "S2": Source(
                id="S2",
                name="Device examination",
                source_type=SourceType.DEVICE,
                content="Device examination identifies the eyewear as an unauthorized electronic device.",
            ),
        }
    elif case == "sparse":
        sources = {
            "S1": Source(
                id="S1",
                name="Assessment timetable",
                source_type=SourceType.DOCUMENT,
                content="The assessment was scheduled for the published examination period.",
            )
        }
    else:
        raise ValueError(f"Unknown smoke fixture: {case!r}")
    return VNextRunInput(
        case_id=f"vnext-smoke-{case}",
        case_context="A single-subject developer smoke fixture.",
        sources=sources,
        rule_preset=smoke_rule_preset(),
    )


def run_smoke(
    client: ModelClient,
    *,
    case: str = "obvious",
) -> tuple[VNextRunResult, ModelCallMetadata, Any]:
    """Make one structured model call and run it through the vNext pipeline."""
    run_input = smoke_run_input(case)
    investigator = VNextInvestigatorModel(client)
    result = VNextInvestigationRunner(investigator).run(run_input)
    if investigator.last_call is None:
        raise RuntimeError("vNext Investigator did not record its model call")
    return result, investigator.last_call.metadata, investigator.last_call.raw_output


def _print_summary(result: VNextRunResult, metadata: ModelCallMetadata) -> None:
    print(f"vNext smoke run: {result.status.value.upper()}")
    print(f"preset: {result.metadata.rule_preset_id}")
    print(f"model: {metadata.model}")
    print("violations:")
    for item in result.violation_assessments:
        print(f"  {item.violation_id}: {item.status.value.upper()} ({item.confidence.value.upper()})")
    print("furthest conclusion:")
    print(f"  {result.furthest_conclusion.statement}")
    print(f"graph updates applied: {result.metadata.proposal_update_count}")
    print(f"input tokens: {metadata.input_tokens}")
    print(f"output tokens: {metadata.output_tokens}")
    print(f"latency seconds: {metadata.latency_seconds:.3f}")
    print(f"stop reason: {metadata.finish_reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one dev-only real-model vNext assessment smoke test.")
    parser.add_argument("--case", choices=("obvious", "sparse"), default="obvious")
    parser.add_argument("--debug", action="store_true", help="Print raw model output and parsed assessment.")
    args = parser.parse_args(argv)
    try:
        model_id = os.environ.get("VNEXT_INVESTIGATOR_MODEL_ID") or None
        client = BedrockModelClient(model_id=model_id)
        result, metadata, raw_output = run_smoke(client, case=args.case)
        _print_summary(result, metadata)
        if args.debug:
            print("raw model output:")
            print(raw_output)
            print("parsed assessment:")
            print(json.dumps(result.model_dump(mode="json"), indent=2))
        return 0
    except Exception as exc:
        print(f"vNext smoke run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
