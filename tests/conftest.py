from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Cost-guard ledger isolation: ChatLLM's module-level default guard
# writes a date-keyed call ledger. Point it at a per-run temp file so
# test runs never touch the repo's real state/cost_ledger.json.
os.environ.setdefault(
    "TEOW_AGL_COST_LEDGER",
    str(Path(tempfile.gettempdir()) / f"teow_test_cost_{uuid.uuid4().hex[:8]}.json"),
)

from teow_agl.adapters.mock_planner import MockPlanner  # noqa: E402
from teow_agl.modules.module_105_human_gate import HumanGate  # noqa: E402
from teow_agl.runtime import Runtime  # noqa: E402
from teow_agl.tools.filesystem_tools import FilesystemTool  # noqa: E402
from teow_agl.tools.mock_tools import MockTool  # noqa: E402
from teow_agl.tools.report_tools import ReportTool  # noqa: E402


@pytest.fixture
def isolated_workspace(tmp_path: Path) -> Path:
    src_root = ROOT
    dst = tmp_path / "build"
    dst.mkdir()
    (dst / "configs").mkdir()
    (dst / "prompts").mkdir()
    (dst / "traces").mkdir()
    (dst / "outputs").mkdir()
    (dst / "workspace").mkdir()
    for f in (src_root / "configs").iterdir():
        # configs/ now contains subdirectories (e.g. domain_packs/) for the
        # domain-pack subsystem, so copy dirs recursively, files flat.
        if f.is_dir():
            shutil.copytree(f, dst / "configs" / f.name)
        else:
            shutil.copy(f, dst / "configs" / f.name)
    for f in (src_root / "prompts").iterdir():
        if f.is_dir():
            shutil.copytree(f, dst / "prompts" / f.name)
        else:
            shutil.copy(f, dst / "prompts" / f.name)
    return dst


def make_runtime(
    workspace: Path,
    *,
    planner=None,
    gate: str | object = "approve_all",
    profile_filename: str = "default_user_governance_profile.json",
) -> Runtime:
    workspace_roots = [str(workspace / "workspace"), str(workspace / "outputs"), str(workspace / "client_exports")]
    (workspace / "client_exports").mkdir(exist_ok=True)
    tools = {
        "fs": FilesystemTool(workspace_roots),
        "report": ReportTool(),
        "docx": MockTool("docx"), "pptx": MockTool("pptx"), "xlsx": MockTool("xlsx"),
        "desktop": MockTool("desktop"),
        "gui": MockTool("gui"),
        "email": MockTool("email"), "publish": MockTool("publish"),
        "code": MockTool("code"), "shell": MockTool("shell"), "human": MockTool("human"),
    }
    rt = Runtime(
        config_dir=workspace / "configs", prompts_dir=workspace / "prompts",
        planner=planner or MockPlanner(), tool_registry=tools,
        human_gate=HumanGate(gate), trace_dir=workspace / "traces",
        profile_filename=profile_filename,
    )
    rt.profile.profile["workspace_roots"] = workspace_roots
    return rt


@pytest.fixture
def make_runtime_factory(isolated_workspace):
    def _factory(**kw):
        return make_runtime(isolated_workspace, **kw)
    return _factory
