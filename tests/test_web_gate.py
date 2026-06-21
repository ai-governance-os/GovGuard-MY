"""WebHumanGate pause/resume."""
from __future__ import annotations

import threading
import time

from teow_agl.models import ApprovalRequest
from teow_agl.modules.module_105_web_gate import WebHumanGate


def _approval():
    return ApprovalRequest(approval_id="appr_x", task_id="t1", action_id="a1",
                           summary="please approve", risk_factors=[], context={},
                           status="pending")


def test_gate_blocks_until_approved():
    gate = WebHumanGate(timeout_seconds=5)
    a = _approval(); result = []
    t = threading.Thread(target=lambda: result.append(gate.review(a)))
    t.start()
    deadline = time.time() + 2
    while time.time() < deadline and not gate.pending_snapshot():
        time.sleep(0.01)
    assert gate.pending_snapshot()
    assert gate.decide("appr_x", "approved", "ok")
    t.join(timeout=3)
    assert result[0].status == "approved"
    assert result[0].human_note == "ok"


def test_gate_rejects_with_note():
    gate = WebHumanGate(timeout_seconds=5)
    a = _approval(); result = []
    t = threading.Thread(target=lambda: result.append(gate.review(a)))
    t.start()
    deadline = time.time() + 2
    while time.time() < deadline and not gate.pending_snapshot():
        time.sleep(0.01)
    assert gate.decide("appr_x", "rejected", "no")
    t.join(timeout=3)
    assert result[0].status == "rejected"


def test_gate_unknown_decision_returns_false():
    gate = WebHumanGate(timeout_seconds=1)
    assert gate.decide("nonexistent", "approved") is False


def test_safe_target_check_empty_target():
    """The very bug that wiped the build: empty target must always be denied."""
    from teow_agl.tools._safety import safe_target_check
    ok, reason = safe_target_check("", ["/some/root"])
    assert not ok
    assert reason == "empty_target"


def test_safe_target_check_workspace_root_itself(tmp_path):
    """Target equal to a workspace root must be denied."""
    from teow_agl.tools._safety import safe_target_check
    ok, reason = safe_target_check(str(tmp_path), [str(tmp_path)])
    assert not ok
    assert reason == "target_is_workspace_root_itself"


def test_safe_target_check_inside_root(tmp_path):
    """Target inside a workspace root must pass."""
    from teow_agl.tools._safety import safe_target_check
    child = tmp_path / "child.txt"
    child.write_text("x")
    ok, reason = safe_target_check(str(child), [str(tmp_path)])
    assert ok
    assert reason == "ok"
