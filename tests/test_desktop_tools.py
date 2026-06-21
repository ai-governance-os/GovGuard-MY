"""Desktop file-control tool."""
from __future__ import annotations

from pathlib import Path

from teow_agl.models import CandidateAction
from teow_agl.tools.desktop_tools import DesktopTool


def _action(operation, target="", metadata=None):
    return CandidateAction(
        action_id="a1", tool="desktop", operation=operation, target=target,
        purpose="t", expected_effect="t", reversibility="medium",
        uncertainty="low", risk_factors=[], requires_governance=True,
        metadata=metadata or {},
    )


def test_list_dir_inside_workspace(tmp_path: Path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    res = DesktopTool([str(tmp_path)])(_action("list_dir", target=str(tmp_path)))
    assert res["status"] == "success"
    names = {r["name"] for r in res["rows"]}
    assert {"a.txt", "sub"} <= names


def test_list_dir_denies_outside(tmp_path: Path):
    outside = tmp_path.parent / "outside_xyz"
    outside.mkdir(exist_ok=True)
    res = DesktopTool([str(tmp_path)])(_action("list_dir", target=str(outside)))
    assert res["status"] == "denied"


def test_make_folder(tmp_path: Path):
    res = DesktopTool([str(tmp_path)])(_action("make_folder", target=str(tmp_path), metadata={"name": "PDFs"}))
    assert res["status"] == "success"
    assert (tmp_path / "PDFs").is_dir()


def test_move_within_workspace(tmp_path: Path):
    src = tmp_path / "report.pdf"; src.write_text("pdf")
    dst_dir = tmp_path / "PDFs"; dst_dir.mkdir()
    res = DesktopTool([str(tmp_path)])(_action("move_file", metadata={"src": str(src), "dst": str(dst_dir)}))
    assert res["status"] == "success"
    assert not src.exists() and (dst_dir / "report.pdf").exists()


def test_delete_inside_workspace(tmp_path: Path):
    f = tmp_path / "junk.txt"; f.write_text("x")
    res = DesktopTool([str(tmp_path)])(_action("delete_file", target=str(f)))
    assert res["status"] == "success"
    assert not f.exists()


def test_delete_denies_outside_workspace(tmp_path: Path):
    other = tmp_path.parent / "outside_del"
    other.mkdir(exist_ok=True)
    f = other / "junk.txt"; f.write_text("x")
    res = DesktopTool([str(tmp_path)])(_action("delete_file", target=str(f)))
    assert res["status"] == "denied"
    assert f.exists()


def test_delete_denies_empty_target(tmp_path: Path):
    res = DesktopTool([str(tmp_path)])(_action("delete_file", target=""))
    assert res["status"] == "denied"
    assert "empty_target" in res.get("error", "")


def test_delete_denies_workspace_root_itself(tmp_path: Path):
    """Critical: must NOT allow deleting the workspace root itself."""
    res = DesktopTool([str(tmp_path)])(_action("delete_file", target=str(tmp_path)))
    assert res["status"] == "denied"
    assert tmp_path.exists()
