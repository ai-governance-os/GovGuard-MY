from __future__ import annotations

import pytest

from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer
from teow_agl.modules.module_school_artifact_guard import (
    classify_school_validation_issues,
    is_clearly_future_advice,
    validate_school_markdown,
)


_QUALIFIED_HEADING = "## Recommended next steps — subject to human review"


class _AuditLLM:
    backend = "test"

    def __init__(self, claim: str) -> None:
        self.claim = claim

    def chat_json(self, **_kwargs) -> dict:
        return {
            "pass": False,
            "unsupported_claims": [{
                "action_id": "incident_report",
                "claim": self.claim,
                "reason": "The appended case assertion is not in the source.",
            }],
        }


def _action(*, role: str = "internal_incident_report") -> CandidateAction:
    return CandidateAction(
        action_id="incident_report",
        tool="fs",
        operation="save_under_outputs",
        purpose="Prepare a governed internal draft.",
        target="incident_report.md",
        metadata={"artifact_role": role, "audience": "internal"},
    )


@pytest.mark.parametrize(
    "source",
    [
        "A pupil had an allergic reaction. Prepare an internal report.",
        "A pupil showed signs of anaphylaxis. Prepare an internal report.",
        "A pupil sustained injuries. Prepare an internal report.",
    ],
)
@pytest.mark.parametrize(
    "advice",
    [
        "Consider administering antihistamine.",
        "Consider giving medication.",
        "Consider applying a cold pack.",
        "Consider monitoring the pupil for 30 minutes.",
    ],
)
def test_qualified_clinical_gerund_remains_a_hard_policy_block(
    source: str,
    advice: str,
) -> None:
    action = _action()
    artifact = (
        "# Internal incident report\n\n"
        f"{_QUALIFIED_HEADING}\n\n"
        f"- {advice}\n\n"
        "Draft only. No clinical action was taken.\n"
    )

    issues = validate_school_markdown(action, artifact, source)
    assert any(
        issue.startswith("unsourced_clinical_instruction:")
        for issue in issues["policy"]
    ), issues

    disposition = classify_school_validation_issues(action, issues)
    assert any(
        "unsourced_clinical_instruction:" in issue
        for issue in disposition["hard_block"]
    ), disposition


@pytest.mark.parametrize(
    "advice",
    [
        (
            "## Recommended next steps — subject to human review\n"
            "- Consider arranging a temporary classroom relocation."
        ),
        (
            "## Recommended next steps — subject to human approval\n"
            "- Consider mediation or conflict resolution strategies to "
            "prevent future occurrences."
        ),
        (
            "## Cadangan — tertakluk kepada semakan manusia\n"
            "- Boleh pertimbangkan sama ada untuk mengatur penempatan sementara."
        ),
        (
            "## 建议的后续步骤 — 须经人工审核\n"
            "- 可以考虑是否安排临时教室。"
        ),
    ],
)
def test_narrow_consider_advice_still_accepts_one_nonassertive_option(
    advice: str,
) -> None:
    assert is_clearly_future_advice(advice) is True


@pytest.mark.parametrize(
    "claim",
    [
        "Consider mediation; the school closed the room.",
        "Consider mediation. The principal signed the report.",
        "Consider arranging a room; staff locked the doors.",
        "Consider support options; the school sent the notice.",
        "Consider mediation and the school closed the room.",
        "Consider arranging a room while staff locked the doors.",
        "Consider mediation, and the family knows about the incident.",
        "Consider support options and the school sent the notice.",
        "Consider support options and staff know the facts.",
        "Consider support options and pupils left the site.",
        "Consider support options: the school sent the notice.",
        "Consider support options — the school sent the notice.",
        "Consider support options (the school closed the room).",
        "Consider support options where the principal signed the report.",
        "Consider support options knowing the family knows about the incident.",
        (
            "Boleh pertimbangkan sama ada untuk mengatur penempatan sementara; "
            "pihak sekolah menutup bilik itu."
        ),
        (
            "Boleh pertimbangkan sama ada untuk mengatur penempatan sementara "
            "dan pihak sekolah menutup bilik itu."
        ),
        "可以考虑是否安排临时教室；学校关闭了教室。",
        "可以考虑是否安排临时教室并且学校关闭了教室。",
    ],
)
def test_narrow_consider_cannot_launder_a_second_case_assertion(
    claim: str,
) -> None:
    context = f"{_QUALIFIED_HEADING}\n- {claim}"
    assert is_clearly_future_advice(context) is False

    action = _action()
    artifact = f"# Internal incident report\n\n{context}\n"
    audit = ContentSynthesizer(
        chat_llm=_AuditLLM(claim),
    )._school_fact_grounding_audit(
        [action],
        {action.action_id: artifact},
        "Prepare an internal incident report from the supplied facts.",
    )
    assert audit["pass"] is False, audit
    assert any(claim in issue for issue in audit["issues"]), audit


@pytest.mark.parametrize(
    "claim",
    [
        "Notify parents that police arrested the teacher.",
        "Inform staff that the ambulance took the pupil to hospital.",
        "Tell families that the doctor diagnosed asthma.",
        "Record that the inspector declared the room safe.",
        "Inform staff after the ambulance took the pupil to hospital.",
    ],
)
def test_operator_advice_cannot_launder_an_embedded_case_assertion(
    claim: str,
) -> None:
    context = f"{_QUALIFIED_HEADING}\n- {claim}"
    assert is_clearly_future_advice(context) is False

    action = _action()
    artifact = f"# Internal incident report\n\n{context}\n"
    audit = ContentSynthesizer(
        chat_llm=_AuditLLM(claim),
    )._school_fact_grounding_audit(
        [action],
        {action.action_id: artifact},
        "Prepare an internal incident report from the supplied facts.",
    )
    assert audit["pass"] is False, audit
    assert any(claim in issue for issue in audit["issues"]), audit


def test_operator_advice_may_wait_for_a_narrow_human_process_boundary() -> None:
    assert is_clearly_future_advice(
        f"{_QUALIFIED_HEADING}\n- Notify parents after human approval."
    ) is True
