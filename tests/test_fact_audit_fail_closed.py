from __future__ import annotations

import pytest

from teow_agl.models import CandidateAction
from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer


class _AuditLLM:
    backend = "openai"

    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, **_kwargs):
        return self.payload


def _audit(payload, *, source="The school was not closed.", artifact="The school was closed."):
    action = CandidateAction(
        action_id="a1", tool="fs", operation="save_under_outputs",
        purpose="draft report", target="report.md",
        metadata={"artifact_role": "internal_incident_report", "audience": "internal"},
    )
    return ContentSynthesizer(chat_llm=_AuditLLM(payload))._school_fact_grounding_audit(
        [action], {"a1": artifact}, source,
    )


@pytest.mark.parametrize("payload", [
    {},
    {"malformed": "x"},
    {"pass": False},
    {"pass": True},
    {"pass": True, "unsupported_claims": "none"},
])
def test_semantic_fact_audit_fails_closed_on_malformed_or_incomplete_json(payload):
    assert _audit(payload)["pass"] is False


def test_semantic_fact_audit_accepts_only_explicit_clean_schema():
    assert _audit({"pass": True, "unsupported_claims": []}) == {
        "pass": True, "issues": [],
    }


@pytest.mark.parametrize(("source", "claim"), [
    ("The school was not closed.", "The school was closed."),
    ("Ali's parent was not contacted.", "Ali's parent was contacted."),
])
def test_semantic_fact_audit_never_discards_a_reported_polarity_error(source, claim):
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1", "claim": claim,
            "reason": "The output reverses the source polarity.",
        }],
    }
    result = _audit(payload, source=source, artifact=claim)
    assert result["pass"] is False
    assert any(claim in issue for issue in result["issues"])


def test_semantic_fact_audit_accepts_source_grounded_negative_paraphrase():
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1",
            "claim": "Maintenance contacted: No (as of time of report)",
            "reason": "Not supported",
        }],
    }
    result = _audit(
        payload,
        source="Maintenance has not yet been contacted.",
        artifact="Maintenance contacted: No (as of time of report)",
    )
    assert result == {"pass": True, "issues": []}


def test_semantic_fact_audit_keeps_proposals_under_parent_heading():
    payload = {
        "pass": False,
        "unsupported_claims": [{
            "action_id": "a1",
            "claim": "Identify an alternative room such as the library or hall.",
            "reason": "The source did not assign a room.",
        }],
    }
    artifact = (
        "# Relocation checklist\n\n"
        "## Proposed arrangements - subject to school approval\n\n"
        "### Before relocation\n\n"
        "- Identify an alternative room such as the library or hall.\n"
    )
    assert _audit(payload, source="Prepare a relocation checklist.", artifact=artifact) == {
        "pass": True, "issues": [],
    }