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
    InvestigatorAssessment,
    VNextInvestigationRunner,
    VNextRunInput,
    VNextRunResult,
    ViolationDefinition,
)


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


def _schema_contract() -> str:
    return json.dumps(InvestigatorAssessment.model_json_schema(), indent=2, sort_keys=True)


def build_prompt(run_input: VNextRunInput) -> str:
    """Build the only prompt used by the real Investigator smoke call."""
    sources = [
        {
            "source_id": source_id,
            "source_type": source.source_type.value,
            "title": source.name,
            "content": source.content or "",
        }
        for source_id, source in sorted(run_input.sources.items())
    ]
    return "\n".join(
        [
            "You are the Investigator for one complete finite assessment.",
            "Evaluate every configured violation exactly once and return the complete assessment in one response.",
            "Do not ask for more evidence, request human input, or produce a follow-up question.",
            "Missing evidence means NOT_CURRENTLY_SUPPORTED, not another enquiry.",
            "A supported narrower violation does not require proof of stronger downstream conduct.",
            "Assess only the prohibited conduct actually defined by each rule.",
            "If possession itself is prohibited, proof of activation or use is not required.",
            "If communication is prohibited, do not require proof of its exact medium or extent.",
            "Evidence discipline: a claim is not automatically a fact; a source statement is not automatically true; association is not collaboration; opportunity is not use; anomaly is not misconduct; absence of evidence is not evidence of absence; unsupported does not mean innocence established.",
            "Raw source IDs identify source records, not automatically established facts. Use graph proposals for any E/P/H/U concepts you need, and reference proposal local_ref values in the assessment after proposing them.",
            "Return JSON only. Use exactly the current schema below. Do not add fields.",
            "\nCASE CONTEXT\n" + (run_input.case_context or ""),
            "\nRULE PRESET\n" + json.dumps(run_input.rule_preset.model_dump(mode="json"), indent=2),
            "\nCURRENT RAW SOURCES\n" + json.dumps(sources, indent=2),
            "\nEXACT INVESTIGATOR ASSESSMENT JSON SCHEMA\n" + _schema_contract(),
        ]
    )


def run_smoke(
    client: ModelClient,
    *,
    case: str = "obvious",
) -> tuple[VNextRunResult, ModelCallMetadata, Any]:
    """Make one structured model call and run it through the vNext pipeline."""
    run_input = smoke_run_input(case)
    call_result = client.call(build_prompt(run_input), InvestigatorAssessment)
    result = VNextInvestigationRunner(lambda _: call_result.parsed).run(run_input)
    return result, call_result.metadata, call_result.raw_output


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
