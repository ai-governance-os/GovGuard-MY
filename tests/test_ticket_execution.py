"""Ticket enforcement."""
from __future__ import annotations

import copy

from teow_agl.models import CandidateAction
from teow_agl.modules.module_107_executor import ExecutionModule
from teow_agl.tools.mock_tools import MockTool
from teow_agl.util.ticket import issue_ticket


def _action():
    return CandidateAction(
        action_id="a1", tool="fs", operation="delete",
        target="/tmp/whatever", purpose="x", expected_effect="x",
        reversibility="low", uncertainty="low",
        risk_factors=[], requires_governance=True, metadata={"task_id": "t1"},
    )


def test_executor_denies_unsigned_ticket():
    executor = ExecutionModule({"fs": MockTool("fs")})
    fake = {
        "ticket_id": "fake", "task_id": "t1", "action_id": "a1", "route": "BLUE",
        "issued_by": "103", "approval_id": None, "action_hash": "deadbeef",
        "expires_at": "9999-12-31T00:00:00+00:00", "constraints": {}, "_signature": "0" * 64,
    }
    res = executor.execute(action=_action(), ticket=fake)
    assert res.status == "denied"


def test_executor_denies_tampered_action_hash():
    real = issue_ticket(task_id="t1", action_id="a1", tool="fs", operation="delete",
                        target="/tmp/whatever", route="BLUE", approval_id=None)
    tampered = copy.deepcopy(real); tampered["action_hash"] = "0" * 64
    res = ExecutionModule({"fs": MockTool("fs")}).execute(action=_action(), ticket=tampered)
    assert res.status == "denied"


def test_executor_denies_when_issued_by_not_103():
    real = issue_ticket(task_id="t1", action_id="a1", tool="fs", operation="delete",
                        target="/tmp/whatever", route="BLUE", approval_id=None)
    real["issued_by"] = "999"
    res = ExecutionModule({"fs": MockTool("fs")}).execute(action=_action(), ticket=real)
    assert res.status == "denied"
