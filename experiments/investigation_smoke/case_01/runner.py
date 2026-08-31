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
from investigator.llm.base import ModelClient, ModelParseError
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
    UncertaintyTransitionType,
)
from investigator.state import CaseState, apply_revision as apply_core_revision
from investigator.services import InvestigationService
from investigator.environments.case_01 import Case1ControlledEnvironment
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
        specificity_basis=proposal.specificity_basis_evidence_ids,
    )


def initial_state(response: InitialResponse) -> CaseState:
    return Case1ControlledEnvironment(Path(__file__).resolve().parent / "artifacts").build_initial_state(response)


def release_selected_artifact(state: CaseState, action_id: str) -> ReleaseRecord:
    return Case1ControlledEnvironment(Path(__file__).resolve().parent / "artifacts").execute_action(state, action_id)


def apply_revision(state: CaseState, response: RevisionResponse) -> CaseState:
    return apply_core_revision(state, response)


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
    environment = Case1ControlledEnvironment(Path(__file__).resolve().parent / "artifacts")
    case_input = environment.initial_case_input()
    first_prompt = environment.initial_prompt()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = Path(results_root) / f"{_safe_model_name(resolved_model_id)}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)
    trace = ControlledRunTrace(model_id=resolved_model_id, initial_case_input=case_input, initial_prompt=first_prompt)
    trace_path = output_dir / "trace.json"

    def failed(current: ControlledRunTrace, stage: str, error: Exception, raw_output=... ) -> Path:
        update = {"failure_stage": stage, "error_message": str(error), "parse_success": False}
        if raw_output is not ...:
            update["initial_raw_model_output" if stage.startswith("initial") else "revision_raw_model_output"] = raw_output
        failed_trace = current.model_copy(update=update)
        _write_trace(trace_path, failed_trace)
        return trace_path

    try:
        service = InvestigationService(active_client, environment)
        try:
            session = service.start_case()
        except ModelParseError as exc:
            return failed(trace, "initial_parse", exc, exc.raw_output)
        except Exception as exc:
            return failed(trace, "initial_call", exc)
        initial_response = session.initial_response
        action = session.pending_action
        trace = trace.model_copy(update={
            "initial_raw_model_output": session.initial_raw_model_output,
            "initial_metadata": session.initial_metadata,
            "initial_response": initial_response,
            "initial_hypothesis_state": session.initial_case_state,
            "selected_action_id": action.action_id,
            "target_uncertainty": initial_response.target_uncertainty,
            "expected_information_value": initial_response.expected_information_value,
            "why_this_action_now": initial_response.why_this_action_now,
        })
        try:
            service.set_human_action(session, action.action_id)
            service.execute_action(session)
            service.propose_revision(session)
        except ModelParseError as exc:
            return failed(trace.model_copy(update={"release": session.pending_release}), "revision_parse", exc, exc.raw_output)
        except Exception as exc:
            return failed(trace.model_copy(update={"release": session.pending_release}), "revision_call", exc)
        revision_response = session.pending_revision
        trace = trace.model_copy(update={
            "release": session.pending_release,
            "revision_prompt": session.revision_prompt,
            "revision_raw_model_output": session.revision_raw_model_output,
            "revision_metadata": session.revision_metadata,
            "revision_response": revision_response,
        })
        try:
            service.apply_revision(session)
        except Exception as exc:
            return failed(trace, "state_apply", exc)
        trace = trace.model_copy(update={
            "final_hypothesis_state": session.case_state,
            "parse_success": True,
            "unsupported_operations": session.unsupported_operations,
        })
    except Exception as exc:
        return failed(trace, "state_apply", exc)
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
