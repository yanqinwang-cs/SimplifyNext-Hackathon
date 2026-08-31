import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from investigator.state.case_state import CaseState


class CaseRepository:
    def __init__(self, root: str | Path = "data/cases") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, case_id: str) -> Path:
        return self.root / f"{case_id}.json"

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

    def exists(self, case_id: str) -> bool:
        return self._path(case_id).is_file()

    def list_case_ids(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.json"))

