"""Real Office tool output."""
from __future__ import annotations

from pathlib import Path

from teow_agl.models import CandidateAction
from teow_agl.tools.office_tools import DocxTool, PptxTool, XlsxTool


def _action(tool, target, metadata):
    return CandidateAction(
        action_id="a1", tool=tool, operation="save_under_outputs",
        target=target, purpose="test", expected_effect="file written",
        reversibility="high", uncertainty="low", risk_factors=[],
        requires_governance=True, metadata=metadata,
    )


def test_docx_writes_real_file(tmp_path: Path):
    target = tmp_path / "doc.docx"
    res = DocxTool([str(tmp_path)])(_action("docx", str(target), {"title": "T", "body": "B"}))
    assert res["status"] == "success"
    assert target.exists()
    assert target.read_bytes()[:2] == b"PK"


def test_pptx_writes_real_file(tmp_path: Path):
    target = tmp_path / "deck.pptx"
    res = PptxTool([str(tmp_path)])(_action("pptx", str(target),
                                              {"title": "Deck", "slides": [{"title": "I", "bullets": ["a"]}]}))
    assert res["status"] == "success"
    assert target.exists()


def test_xlsx_writes_real_file(tmp_path: Path):
    target = tmp_path / "data.xlsx"
    res = XlsxTool([str(tmp_path)])(_action("xlsx", str(target), {"sheets": {"S": [["a", 1]]}}))
    assert res["status"] == "success"
    assert target.exists()


def test_docx_denies_empty_target(tmp_path: Path):
    res = DocxTool([str(tmp_path)])(_action("docx", "", {}))
    assert res["status"] == "denied"
    assert "empty_target" in res.get("error", "")


def test_docx_denies_outside_workspace(tmp_path: Path):
    other = tmp_path.parent / "outside_dir"
    other.mkdir(exist_ok=True)
    res = DocxTool([str(tmp_path)])(_action("docx", str(other / "x.docx"), {}))
    assert res["status"] == "denied"
