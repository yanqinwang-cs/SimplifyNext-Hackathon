import importlib

from app.demo_data import ACTIVE_HYPOTHESES, CLAIMS_TO_CHECK, EVIDENCE, KEY_UNCERTAINTIES


def test_streamlit_app_imports_without_model_or_aws_calls() -> None:
    module = importlib.import_module("app.streamlit_app")
    assert hasattr(module, "main")


def test_demo_data_has_the_read_only_case_summary() -> None:
    assert [item[0] for item in ACTIVE_HYPOTHESES] == ["H1", "H2"]
    assert [item[0] for item in KEY_UNCERTAINTIES] == ["U1", "U2"]
    assert [item[0] for item in CLAIMS_TO_CHECK] == ["C1", "C2"]
    assert [item[0] for item in EVIDENCE] == [f"E{i}" for i in range(1, 8)]
