"""Adversarial contracts for server-authorised school-case continuations."""
from __future__ import annotations

from types import SimpleNamespace
import uuid

from fastapi import HTTPException
import pytest

import server.app as appmod


def _parent_state(
    *,
    status: str = "done",
    error: str | None = None,
    ready_context: bool = True,
) -> appmod.TaskState:
    task_id = f"test_parent_{uuid.uuid4().hex}"
    return appmod.TaskState(
        task_id=task_id,
        raw_goal="A pupil was injured. Prepare the governed response pack.",
        started_at="2026-07-18T00:00:00+00:00",
        status=status,
        error=error,
        school_semantics={
            "checked": True,
            "school_domain": True,
            "case_relation": "new_case",
            "source": "test",
            "situation": {"family": "health_medical"},
        } if ready_context else None,
        school_situation={
            "active": True,
            "family": "health_medical",
            "known_facts": [],
            "unknowns": [],
        } if ready_context else None,
        response_pack={
            "revision": 1,
            "state": "ready",
            "deliverables": [{
                "deliverable_id": "incident",
                "artifact_role": "internal_incident_report",
                "kind": "artifact",
            }],
            "critical_question": None,
        } if ready_context else None,
        case_context_id=(f"case_{uuid.uuid4().hex}" if ready_context else None),
    )


@pytest.fixture
def register_parent():
    registered: list[str] = []

    def _register(parent: appmod.TaskState) -> appmod.TaskState:
        with appmod._app_state["lock"]:
            appmod._app_state["tasks"][parent.task_id] = parent
        registered.append(parent.task_id)
        return parent

    yield _register

    with appmod._app_state["lock"]:
        for task_id in registered:
            appmod._app_state["tasks"].pop(task_id, None)


def test_json_cannot_forge_private_continuation_capability() -> None:
    request = appmod.StartTaskRequest.model_validate({
        "raw_goal": "Continue it.",
        "parent_task_id": "attacker_selected_parent",
        "clarification_answers": {"immediate_danger": "No"},
        "_trusted_continuation": True,
        "_trusted_case_context_id": "attacker_selected_case",
        "_trusted_kind": "clarification",
    })
    assert request._trusted_continuation is False
    assert request._trusted_case_context_id is None
    assert request._trusted_kind is None
    assert not any(
        key.startswith("_trusted_") for key in request.model_dump()
    )
    with pytest.raises(HTTPException) as exc_info:
        appmod.start_task(request)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "internal_continuation_only"


def test_public_start_endpoint_cannot_forge_delta_mode() -> None:
    request = appmod.StartTaskRequest(
        raw_goal="Add a ministry report.",
        parent_task_id="attacker_selected_parent",
        response_pack_mode="delta",
        custom_deliverables=[{"label": "Ministry report"}],
    )
    with pytest.raises(HTTPException) as exc_info:
        appmod.start_task(request)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "internal_continuation_only"


@pytest.mark.parametrize(
    ("status", "error", "ready_context", "expected"),
    [
        ("error", "provider failed", True, "parent_task_failed"),
        ("running", None, True, "parent_case_not_ready"),
        ("awaiting_clarification", None, True, "parent_case_not_ready"),
        ("done", None, False, "parent_case_not_context_ready"),
    ],
)
def test_unready_or_failed_parent_cannot_be_inherited(
    register_parent,
    status: str,
    error: str | None,
    ready_context: bool,
    expected: str,
) -> None:
    parent = register_parent(_parent_state(
        status=status, error=error, ready_context=ready_context,
    ))
    with pytest.raises(HTTPException) as exc_info:
        appmod.start_task(appmod.StartTaskRequest(
            raw_goal="Make the report shorter.",
            parent_task_id=parent.task_id,
        ))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == expected


def test_trusted_child_must_match_parent_case_context(register_parent) -> None:
    parent = register_parent(_parent_state())
    request = appmod.StartTaskRequest(
        raw_goal="Additional governed output request: authority report.",
        parent_task_id=parent.task_id,
        response_pack_mode="delta",
        custom_deliverables=[{"label": "Authority report"}],
    )
    request._trusted_continuation = True
    request._trusted_case_context_id = "different_case"
    request._trusted_kind = "delta"
    with pytest.raises(HTTPException) as exc_info:
        appmod.start_task(request)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "trusted_continuation_context_mismatch"


def test_trusted_semantics_preserve_parent_classification_and_force_followup() -> None:
    parent = {
        "checked": False,
        "school_domain": False,
        "case_relation": "new_case",
        "source": "old",
        "situation": {"family": "safety_emergency"},
    }
    semantics = appmod._trusted_continuation_semantics(
        parent, kind="clarification",
    )
    assert semantics["checked"] is True
    assert semantics["school_domain"] is True
    assert semantics["case_relation"] == "follow_up"
    assert semantics["source"] == "trusted_internal_clarification_continuation"
    assert semantics["situation"] == {"family": "safety_emergency"}


def test_confirm_endpoint_mints_nonserialisable_same_case_capability(
    register_parent, monkeypatch,
) -> None:
    parent = _parent_state(status="awaiting_clarification")
    parent.response_pack.update({
        "revision": 7,
        "state": "needs_clarification",
        "critical_question": {"question_id": "immediate_danger"},
    })
    parent = register_parent(parent)
    captured: dict = {}

    def fake_start(request: appmod.StartTaskRequest) -> dict:
        captured["request"] = request
        return {"task_id": "trusted_child"}

    monkeypatch.setattr(appmod, "start_task", fake_start)
    monkeypatch.setattr(appmod._tasks_store, "append", lambda _value: None)
    result = appmod.confirm_response_pack(
        parent.task_id,
        appmod.ConfirmResponsePackRequest(
            revision=7,
            question_id="immediate_danger",
            answer="No",
            selected_deliverable_ids=["incident"],
        ),
    )
    request = captured["request"]
    assert result["task_id"] == "trusted_child"
    assert request._trusted_continuation is True
    assert request._trusted_case_context_id == parent.case_context_id
    assert request._trusted_kind == "clarification"
    assert not any(
        key.startswith("_trusted_") for key in request.model_dump()
    )


def test_add_deliverables_endpoint_mints_delta_capability(
    register_parent, monkeypatch,
) -> None:
    parent = register_parent(_parent_state())
    captured: dict = {}

    def fake_start(request: appmod.StartTaskRequest) -> dict:
        captured["request"] = request
        return {"task_id": "trusted_delta"}

    monkeypatch.setattr(appmod, "start_task", fake_start)
    result = appmod.add_deliverables(
        parent.task_id,
        appmod.AddDeliverablesRequest(
            deliverables=[{"label": "Education authority report"}],
        ),
    )
    request = captured["request"]
    assert result["task_id"] == "trusted_delta"
    assert request.response_pack_mode == "delta"
    assert request._trusted_continuation is True
    assert request._trusted_case_context_id == parent.case_context_id
    assert request._trusted_kind == "delta"


def test_hard_stopped_parent_cannot_mint_delta_capability(
    register_parent,
) -> None:
    parent = _parent_state()
    parent.decisions = [{
        "action_id": "pre_governance_hard_block",
        "route": "RED",
        "reasons": ["pre_governance_hard_block:credential_or_secret"],
    }]
    parent = register_parent(parent)
    with pytest.raises(HTTPException) as exc_info:
        appmod.add_deliverables(
            parent.task_id,
            appmod.AddDeliverablesRequest(
                deliverables=[{"label": "School report"}],
            ),
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "parent_task_hard_stopped"


def test_hard_stop_clears_unexecuted_compiler_proposal() -> None:
    state = _parent_state()
    state.context_workflow_id = "old-workflow"
    result = SimpleNamespace(
        pre_assessment=SimpleNamespace(hard_block=True),
    )
    assert appmod._clear_unexecuted_school_proposal_after_hard_stop(
        state, result,
    ) is True
    assert state.school_situation is None
    assert state.response_pack is None
    assert state.context_workflow_id is None


def test_non_hard_stop_keeps_compiler_proposal() -> None:
    state = _parent_state()
    result = SimpleNamespace(
        pre_assessment=SimpleNamespace(hard_block=False),
    )
    assert appmod._clear_unexecuted_school_proposal_after_hard_stop(
        state, result,
    ) is False
    assert state.school_situation is not None
    assert state.response_pack is not None
