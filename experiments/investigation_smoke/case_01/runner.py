import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from dotenv import load_dotenv

# Keep the documented direct script invocation import-safe.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from experiments.investigation_smoke.case_01.artifacts import render_artifact
from experiments.investigation_smoke.case_01.catalog import artifact_path, get_action
from experiments.investigation_smoke.case_01.prompts import initial_prompt, revision_prompt, visible_case_input
from experiments.investigation_smoke.case_01.schemas import (
    ControlledRunTrace,
    HypothesisProposal,
    InitialResponse,
    ReleaseRecord,
    RevisionResponse,
)
from investigator.llm.base import ModelClient
from investigator.llm.bedrock import BedrockModelClient
from investigator.models import (
    EvidenceItem,
    EvidenceKind,
    Hypothesis,
    HypothesisOrigin,
    HypothesisStatus,
    Source,
    SourceType,
    Uncertainty,
    UncertaintyKind,
)
from investigator.state import CaseState, apply_hypothesis_updates
from experiments.model_screen.cases import get_case


def _safe_model_name(model_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model_id).strip("._") or "model"


def _uncertainties_for(proposal: HypothesisProposal) -> list[Uncertainty]:
    return [
        Uncertainty(
            id=f"{proposal.id}:U{index}",
            kind=UncertaintyKind.UNKNOWN,
            description=description,
        )
        for index, description in enumerate(proposal.unresolved, start=1)
    ]


def _hypothesis_from_proposal(proposal: HypothesisProposal) -> Hypothesis:
    return Hypothesis(
        id=proposal.id,
        parent_id=proposal.parent_id,
        statement=proposal.statement,
        origin=HypothesisOrigin.AGENT_SUGGESTION,
        status=HypothesisStatus(proposal.status),
        supporting_evidence_ids=proposal.supported_by,
        conflicting_evidence_ids=proposal.conflicted_by,
        unresolved_issue_ids=[f"{proposal.id}:U{index}" for index in range(1, len(proposal.unresolved) + 1)],
        specificity_basis=proposal.specificity_basis,
    )


def initial_state(response: InitialResponse) -> CaseState:
    case = get_case("case_01")
    visible_source = Source(id="case_01_visible", name="Case 01 visible evidence", source_type=SourceType.OTHER)
    evidence = {
        f"E{index}": EvidenceItem(
            id=f"E{index}", source_id=visible_source.id, raw_content=content,
            kind=EvidenceKind.OTHER,
        )
        for index, content in enumerate(case["evidence"], start=1)
    }
    uncertainties = {
        uncertainty.id: uncertainty
        for proposal in response.hypotheses
        for uncertainty in _uncertainties_for(proposal)
    }
    state = CaseState(
        case_id="case_01", title=case["title"], sources={visible_source.id: visible_source},
        evidence=evidence,
        hypotheses={proposal.id: _hypothesis_from_proposal(proposal) for proposal in response.hypotheses},
        uncertainties=uncertainties,
    )
    return state


def release_selected_artifact(state: CaseState, action_id: str) -> ReleaseRecord:
    action = get_action(action_id)
    path = artifact_path(action_id)
    source = Source(id=f"{action_id}_source", name=action.title, source_type=SourceType.OTHER, metadata={"source_type": action.source_type})
    content = render_artifact(path)
    state.sources[source.id] = source
    evidence_id = f"{action_id}_RELEASE"
    state.evidence[evidence_id] = EvidenceItem(
        id=evidence_id, source_id=source.id, raw_content=content,
        kind=EvidenceKind.OTHER, metadata={"action_id": action_id, "artifact": action.artifact_filename},
    )
    return ReleaseRecord(
        action_id=action_id, artifact_id=evidence_id,
        artifact_path=str(path), source_type=action.source_type, content=content,
    )


def apply_revision(state: CaseState, response: RevisionResponse) -> CaseState:
    updated = apply_hypothesis_updates(state, response.hypothesis_updates)
    for proposal in response.new_hypotheses:
        if proposal.id in updated.hypotheses:
            raise ValueError(f"Duplicate hypothesis ID in revision: {proposal.id!r}")
        for uncertainty in _uncertainties_for(proposal):
            if uncertainty.id in updated.uncertainties:
                raise ValueError(f"Duplicate uncertainty ID in revision: {uncertainty.id!r}")
            updated.uncertainties[uncertainty.id] = uncertainty
        updated.hypotheses[proposal.id] = _hypothesis_from_proposal(proposal)
    return CaseState.model_validate(updated.model_dump())


def _write_trace(path: Path, trace: ControlledRunTrace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        temporary = Path(handle.name)
        json.dump(trace.model_dump(mode="json"), handle, indent=2)
        handle.write("\n")
    temporary.replace(path)


def run(
    model_id: str | None,
    results_root: str | Path = "experiments/investigation_smoke/results",
    client: ModelClient | None = None,
) -> Path:
    load_dotenv()
    active_client = client or BedrockModelClient(model_id=model_id)
    resolved_model_id = model_id or getattr(active_client, "model_id", "unknown-model")
    case_input = visible_case_input()
    first_prompt = initial_prompt()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = Path(results_root) / f"{_safe_model_name(resolved_model_id)}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)
    trace = ControlledRunTrace(model_id=resolved_model_id, initial_case_input=case_input, initial_prompt=first_prompt)

    try:
        first_call = active_client.call(first_prompt, InitialResponse)
        trace = trace.model_copy(update={
            "initial_raw_model_output": first_call.raw_output,
            "initial_metadata": first_call.metadata,
        })
        initial_response = InitialResponse.model_validate(first_call.parsed)
        action = get_action(initial_response.selected_action_id)
        state = initial_state(initial_response)
        release = release_selected_artifact(state, action.action_id)
        selected_action = {"action_id": action.action_id, "title": action.title, "definition": action.definition}
        second_prompt = revision_prompt(case_input, state.model_dump(mode="json"), selected_action, release.content)
        trace = trace.model_copy(update={
            "initial_response": initial_response,
            "initial_hypothesis_state": state,
            "selected_action_id": action.action_id,
            "target_uncertainty": initial_response.target_uncertainty,
            "expected_information_value": initial_response.expected_information_value,
            "why_this_action_now": initial_response.why_this_action_now,
            "release": release,
            "revision_prompt": second_prompt,
        })
        second_call = active_client.call(second_prompt, RevisionResponse)
        trace = trace.model_copy(update={
            "revision_raw_model_output": second_call.raw_output,
            "revision_metadata": second_call.metadata,
        })
        revision_response = RevisionResponse.model_validate(second_call.parsed)
        final_state = apply_revision(state, revision_response)
        trace = trace.model_copy(update={
            "revision_response": revision_response,
            "final_hypothesis_state": final_state,
            "parse_success": True,
        })
    except Exception as exc:
        trace = trace.model_copy(update={"error_message": str(exc), "parse_success": False})
    trace_path = output_dir / "trace.json"
    _write_trace(trace_path, trace)
    return trace_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the controlled two-call Case 01 investigation smoke test.")
    parser.add_argument("--model", help="Bedrock model or inference-profile ID; defaults to BEDROCK_MODEL_ID")
    parser.add_argument("--results-root", default="experiments/investigation_smoke/results")
    args = parser.parse_args()
    print(run(args.model, args.results_root))


if __name__ == "__main__":
    main()
