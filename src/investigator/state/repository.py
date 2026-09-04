import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from investigator.state.case_state import CaseState


class CaseRepository:
    CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    RUN_ID_RE = re.compile(r"^run_\d{6}$")
    def __init__(self, root: str | Path = "data/cases") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate_case_id(cls, case_id: str) -> str:
        if not isinstance(case_id, str) or not cls.CASE_ID_RE.fullmatch(case_id):
            raise ValueError("Invalid case identifier")
        return case_id

    @classmethod
    def validate_run_id(cls, run_id: str) -> str:
        if not isinstance(run_id, str) or not cls.RUN_ID_RE.fullmatch(run_id):
            raise ValueError("Invalid run identifier")
        return run_id

    def _contained(self, path: Path) -> Path:
        resolved_root = self.root.resolve()
        resolved = path.resolve()
        if resolved != resolved_root and resolved_root not in resolved.parents:
            raise ValueError("Path escapes repository root")
        return path

    def case_path(self, case_id: str) -> Path:
        return self._contained(self.root / f"{self.validate_case_id(case_id)}.json")

    def case_artifact_dir(self, case_id: str) -> Path:
        return self._contained(self.root / self.validate_case_id(case_id))

    def runs_dir(self, case_id: str) -> Path:
        return self._contained(self.case_artifact_dir(case_id) / "runs")

    def run_dir(self, case_id: str, run_id: str) -> Path:
        return self._contained(self.runs_dir(case_id) / self.validate_run_id(run_id))

    def _path(self, case_id: str) -> Path:
        return self.case_path(case_id)

    def save(self, case_state: CaseState) -> None:
        destination = self._path(case_state.case_id)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.root, delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            json.dump(case_state.model_dump(mode="json"), handle, indent=2)
            handle.write("\n")
        temporary.replace(destination)

    def load(self, case_id: str) -> CaseState:
        try:
            with self._path(case_id).open(encoding="utf-8") as handle:
                return CaseState.model_validate(json.load(handle))
        except FileNotFoundError as exc:
            raise KeyError(f"Case {case_id!r} does not exist") from exc

    def require_case(self, case_id: str) -> CaseState:
        self.validate_case_id(case_id)
        try:
            return self.load(case_id)
        except KeyError as exc:
            raise KeyError("Case does not exist") from exc

    def exists(self, case_id: str) -> bool:
        return self._path(case_id).is_file()

    def list_case_ids(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.json") if self.CASE_ID_RE.fullmatch(path.stem))
