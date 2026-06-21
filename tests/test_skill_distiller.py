"""Module 109B — Skill Distiller (Phase 1A) unit tests.

Coverage targets the four layers of decision that gate a skill proposal:

  (1) The 8 trigger checks across 6 dimensions in `_should_propose`. One
      negative test per check + a single happy-path positive test.
  (2) The Layer-1 PII gate (`_pii_scan_propose`). Two sub-layers:
      - forbidden_patterns from skill_constraints.json (prompt injection,
        API key shapes) — hard reject.
      - pii_extra_patterns.hard_reject (email/phone/CC/national IDs) — hard
        reject WITH category-named audit event.
      - pii_extra_patterns.redact (paths/URL credentials) — sanitize +
        propagate. The redact pass must NOT use the matched text as a
        regex template (the `\\` in `<USER_HOME>\\` would otherwise raise
        re.error mid-scan); the implementation uses a lambda for literal
        substitution and we assert that path works.
  (3) The public `scan_text` wrapper used by the Layer-2 PII gate in
      server/app.py — same engine, different invocation site.
  (4) The daily-rate counter (in-process, UTC-day reset).

We deliberately do NOT spin up a full Runtime here — the Distiller is
designed to be a side-effect-free pure function over (task_result,
plan_shape). The runtime-integration assertion lives in
test_skill_manager.py / runtime tests; here we exercise just the
module's contract.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from teow_agl.modules.module_109b_skill_distiller import SkillDistiller
from teow_agl.modules.module_skill_manager import SkillManager
from teow_agl.policies.subject_confidence import SubjectConfidence


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeChatLLM:
    """Returns a fixed JSON-shaped dict from chat_json. The Distiller's
    draft step delegates entirely to chat_json — no other call shape
    matters for these tests."""

    backend = "fake-llm"

    def __init__(self, payload: dict | None = None,
                 raise_on_call: bool = False) -> None:
        self._payload = payload or {
            "name": "save-as-docx",
            "description": "Save the agent's text output as a docx file.",
            "procedure": (
                "1. Capture the LLM output as plain text.\n"
                "2. Pass it to docx.save_under_outputs with a clean title.\n"
                "3. Verify the resulting file exists and is non-empty.\n"
                "4. Return the absolute path so downstream tasks can link it."
            ),
            "tags": ["docx", "office", "save"],
        }
        self._raise = raise_on_call
        self.calls: list[tuple[str, str]] = []

    def chat_json(self, *, system: str, user: str,
                  max_tokens: int = 1500) -> dict:
        self.calls.append((system, user))
        if self._raise:
            raise RuntimeError("simulated LLM error")
        return self._payload


def _constraints(**overrides) -> dict:
    """Minimal-but-realistic copy of skill_constraints.json."""
    base = {
        "creation_limits": {
            "max_skills_per_task": 1,
            "max_chars_per_skill": 2000,
            "min_chars_per_skill": 60,
            "max_total_skills": 200,
        },
        "retrieval": {"top_k_injected": 3, "min_score_for_injection": 0.0},
        "lifecycle": {"stale_after_days": 30,
                      "auto_archive_enabled": False,
                      "auto_delete_enabled": False},
        "forbidden_patterns": {
            "patterns": [
                r"(?i)\bignore (previous|all|above) instructions\b",
                r"(?i)\bapi[_ -]?key\b\s*[:=]",
                r"(?i)sk-[a-z0-9]{20,}",
            ],
        },
        "min_task_quality": {
            "require_blue_or_green_route": True,
            "min_executions": 1,
            "skip_if_verification_failed": True,
        },
        "distiller": {
            "enabled": True,
            "exclude_categories": ["greeting", "identity_capability"],
            "min_successes": 3,
            "min_success_rate": 0.80,
            "recent_window": 5,
            "max_per_day": 3,
            "draft_max_tokens": 800,
        },
        "pii_extra_patterns": {
            "hard_reject": {
                "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
                "phone_intl": r"\+\d{1,3}[-\s]?\(?\d{1,4}\)?[-\s]?\d{3,4}[-\s]?\d{3,4}",
                "credit_card": r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}",
                "sg_nric": r"[STFG]\d{7}[A-Z]",
            },
            "redact": {
                "abs_path_win": {
                    "pattern": r"[A-Za-z]:\\Users\\[^\\]+\\",
                    "replacement": "<USER_HOME>\\",
                },
                "abs_path_unix": {
                    "pattern": r"/home/[^/]+/",
                    "replacement": "<USER_HOME>/",
                },
            },
        },
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def _seed_successes(sc: SubjectConfidence, category: str, n: int) -> None:
    for i in range(n):
        sc.record(category=category, outcome="success",
                  task_id=f"t_{category}_{i}")


def _make_distiller(tmp_path: Path, *,
                    sc_seed: int = 10,
                    sc_category: str = "office_doc",
                    constraints_overrides: dict | None = None,
                    chat_payload: dict | None = None,
                    llm_raises: bool = False,
                    ) -> tuple[SkillDistiller, SkillManager,
                               SubjectConfidence, FakeChatLLM]:
    sc = SubjectConfidence(tmp_path / "subject_confidence.jsonl")
    if sc_seed > 0:
        _seed_successes(sc, sc_category, sc_seed)
    sm = SkillManager(tmp_path / "skills",
                      constraints=_constraints(**(constraints_overrides or {})))
    llm = FakeChatLLM(chat_payload, raise_on_call=llm_raises)
    distiller = SkillDistiller(
        chat_llm=llm,
        skill_manager=sm,
        subject_confidence=sc,
        constraints=_constraints(**(constraints_overrides or {})),
    )
    return distiller, sm, sc, llm


def _task_result(*, category: str = "office_doc",
                 verifier_pass: bool = True,
                 final_route: str = "BLUE",
                 task_id: str = "task_demo") -> SimpleNamespace:
    """Build a minimal stub that the Distiller can read via getattr.

    The Distiller only touches: task_category, verification, final_route,
    plan, executions, user_intent, task_id. None of which need to be
    real models — getattr-shaped stubs are enough.
    """
    return SimpleNamespace(
        task_id=task_id,
        task_category=category,
        user_intent="Save my notes as a docx in outputs/",
        verification={"pass": verifier_pass, "enabled": True},
        final_route=final_route,
        plan=SimpleNamespace(actions=[
            SimpleNamespace(action_id="a1", tool="docx",
                            operation="save_under_outputs"),
            SimpleNamespace(action_id="a2", tool="filesystem",
                            operation="confirm_exists"),
        ]),
        executions=[
            SimpleNamespace(action_id="a1", status="success",
                            output_summary="wrote outputs/notes.docx"),
            SimpleNamespace(action_id="a2", status="success",
                            output_summary="exists"),
        ],
    )


# ===========================================================================
# (1) 8 trigger checks across 6 dimensions
# ===========================================================================

def test_happy_path_yields_proposal(tmp_path: Path):
    """All 8 checks pass → returns a create_skill proposal."""
    distiller, _, _, llm = _make_distiller(tmp_path, sc_seed=10)
    proposal = distiller.maybe_propose(task_result=_task_result(),
                                       plan_shape="shape_a")
    assert proposal is not None
    assert proposal["kind"] == "create_skill"
    assert proposal["source_category"] == "office_doc"
    assert proposal["source_shape"] == "shape_a"
    assert proposal["name"] == "save-as-docx"
    assert "docx.save_under_outputs" in proposal["procedure"]
    # LLM was actually called (we didn't return None from a check first)
    assert len(llm.calls) == 1


def test_check1_disabled_short_circuits(tmp_path: Path):
    distiller, _, _, llm = _make_distiller(
        tmp_path, constraints_overrides={"distiller": {"enabled": False}})
    assert distiller.maybe_propose(task_result=_task_result()) is None
    assert llm.calls == []  # LLM never invoked when gated


def test_check2_excluded_category(tmp_path: Path):
    distiller, _, _, llm = _make_distiller(tmp_path, sc_category="greeting")
    out = distiller.maybe_propose(
        task_result=_task_result(category="greeting"))
    assert out is None
    assert llm.calls == []


def test_check3_verification_failed(tmp_path: Path):
    distiller, _, _, llm = _make_distiller(tmp_path)
    out = distiller.maybe_propose(
        task_result=_task_result(verifier_pass=False))
    assert out is None
    assert llm.calls == []


def test_check4_non_blue_green_route(tmp_path: Path):
    distiller, _, _, llm = _make_distiller(tmp_path)
    out = distiller.maybe_propose(
        task_result=_task_result(final_route="INFEASIBLE"))
    assert out is None
    assert llm.calls == []


def test_check5_below_min_successes(tmp_path: Path):
    distiller, _, _, llm = _make_distiller(tmp_path, sc_seed=2)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is None
    assert llm.calls == []


def test_check6_low_success_rate(tmp_path: Path):
    """Cumulative success rate of 50% (below 0.8 threshold) → no proposal."""
    distiller, _, sc, llm = _make_distiller(tmp_path, sc_seed=5)
    # Add 5 failures alongside the 5 already-seeded successes
    for i in range(5):
        sc.record(category="office_doc", outcome="failure",
                  task_id=f"f_{i}")
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is None
    assert llm.calls == []


def test_check7_recent_failure_in_window(tmp_path: Path):
    """A failure within the recent_window kills the proposal even when
    cumulative stats look fine — a category in a recent failure streak
    shouldn't have a new skill cemented for it."""
    distiller, _, sc, llm = _make_distiller(tmp_path, sc_seed=10)
    # Last outcome is a failure → trips check 7
    sc.record(category="office_doc", outcome="failure", task_id="recent_fail")
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is None
    assert llm.calls == []


def test_check8a_existing_skill_for_same_shape(tmp_path: Path):
    """find_active_for hits → skip (dedupe)."""
    distiller, sm, _, llm = _make_distiller(tmp_path, sc_seed=10)
    # Pre-seed an active skill for (office_doc, shape_a)
    sm.create_skill(
        name="existing-save-docx",
        description="Already on file for this shape.",
        procedure="x" * 80,
        task_id="task_prev",
        task_quality={"final_route": "BLUE",
                      "verification_failed": False,
                      "execution_success_count": 1},
        source_category="office_doc", source_shape="shape_a",
    )
    out = distiller.maybe_propose(task_result=_task_result(),
                                  plan_shape="shape_a")
    assert out is None
    assert llm.calls == []


def test_check8b_daily_rate_limit(tmp_path: Path):
    """Once max_per_day proposals fire today, further ones are blocked."""
    distiller, _, _, llm = _make_distiller(
        tmp_path, sc_seed=10,
        constraints_overrides={"distiller": {"max_per_day": 2}})
    # Two successful proposals — different shapes so dedupe doesn't trip
    p1 = distiller.maybe_propose(task_result=_task_result(),
                                 plan_shape="shape_a")
    p2 = distiller.maybe_propose(task_result=_task_result(),
                                 plan_shape="shape_b")
    p3 = distiller.maybe_propose(task_result=_task_result(),
                                 plan_shape="shape_c")
    assert p1 is not None and p1["kind"] == "create_skill"
    assert p2 is not None and p2["kind"] == "create_skill"
    assert p3 is None  # rate-limit kicks in on the 3rd
    assert len(llm.calls) == 2  # the 3rd never reached the LLM


# ===========================================================================
# (2) Layer-1 PII gate — hard_reject
# ===========================================================================

def test_pii_hard_reject_email_in_procedure(tmp_path: Path):
    payload = {
        "name": "contact-flow",
        "description": "Email the customer once docx is ready.",
        "procedure": (
            "1. Build the docx.\n"
            "2. Send it to ops@example.com via the mail tool.\n"
            "3. Confirm delivery.\n"
            "4. Archive the conversation."
        ),
        "tags": ["email", "office"],
    }
    distiller, _, _, _ = _make_distiller(
        tmp_path, sc_seed=10, chat_payload=payload)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None
    assert out["kind"] == "create_skill_blocked"
    assert out["field"] == "procedure"
    assert any("pii_email" in evt for evt in out["audit"])


def test_pii_hard_reject_phone_in_description(tmp_path: Path):
    payload = {
        "name": "call-then-doc",
        "description": "Phone +65-9123-4567 then write up the call notes.",
        "procedure": ("1. Call the number.\n2. Note the answer.\n"
                      "3. Write a docx with what was said.\n"
                      "4. Save to outputs/."),
        "tags": ["call"],
    }
    distiller, _, _, _ = _make_distiller(
        tmp_path, sc_seed=10, chat_payload=payload)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None
    assert out["kind"] == "create_skill_blocked"
    assert out["field"] == "description"
    assert any("pii_phone_intl" in evt for evt in out["audit"])


def test_pii_hard_reject_credit_card(tmp_path: Path):
    payload = {
        "name": "ccard-leak",
        "description": "tries to bake a CC into the procedure.",
        "procedure": (
            "1. Charge 4111-1111-1111-1111 to the account.\n"
            "2. Confirm via api.\n3. Email user.\n4. Done."),
        "tags": ["payment"],
    }
    distiller, _, _, _ = _make_distiller(
        tmp_path, sc_seed=10, chat_payload=payload)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out["kind"] == "create_skill_blocked"
    assert any("pii_credit_card" in evt for evt in out["audit"])


def test_pii_hard_reject_sg_nric(tmp_path: Path):
    payload = {
        "name": "nric-leak",
        "description": "Identify the user by NRIC.",
        "procedure": ("1. Read NRIC S1234567A.\n2. Save.\n"
                      "3. Continue.\n4. End."),
        "tags": ["id"],
    }
    distiller, _, _, _ = _make_distiller(
        tmp_path, sc_seed=10, chat_payload=payload)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out["kind"] == "create_skill_blocked"
    assert any("pii_sg_nric" in evt for evt in out["audit"])


def test_pii_forbidden_patterns_layer_blocks_api_key(tmp_path: Path):
    """forbidden_patterns runs BEFORE hard_reject; an API key shape
    triggers the forbidden-pattern audit, not the pii_* audit."""
    payload = {
        "name": "leak-key",
        "description": "Save credentials.",
        "procedure": ("1. Set api_key=sk-abcdefghij1234567890.\n"
                      "2. Use it.\n3. Forget it.\n4. Move on."),
        "tags": ["auth"],
    }
    distiller, _, _, _ = _make_distiller(
        tmp_path, sc_seed=10, chat_payload=payload)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out["kind"] == "create_skill_blocked"
    assert any("forbidden_pattern" in evt for evt in out["audit"])


# ===========================================================================
# (2) Layer-1 PII gate — redact (must propagate cleaned text back)
# ===========================================================================

def test_pii_redact_user_home_path_propagates(tmp_path: Path):
    """A path like C:\\Users\\Alice\\... is redacted to <USER_HOME>\\... in
    the persisted procedure. The proposal must contain the CLEANED text;
    if the raw path leaked through, downstream skills would carry
    user-specific filesystem layout and break on other machines.

    Also covers the lambda-replacement fix: a literal trailing `\\` in
    the replacement string would raise re.error if treated as a regex
    template. The fix uses a lambda so we substitute literally.
    """
    payload = {
        "name": "save-from-desktop",
        "description": "Read a file from the user's desktop and save a copy.",
        "procedure": (
            "1. Read source from C:\\Users\\Alice\\Desktop\\notes.txt.\n"
            "2. Rewrite as docx via docx.save_under_outputs.\n"
            "3. Confirm file exists.\n"
            "4. Return the absolute output path."
        ),
        "tags": ["filesystem", "docx"],
    }
    distiller, _, _, _ = _make_distiller(
        tmp_path, sc_seed=10, chat_payload=payload)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None
    assert out["kind"] == "create_skill"  # redact KEEPS the proposal
    assert "Alice" not in out["procedure"]
    assert "<USER_HOME>" in out["procedure"]
    assert any("redacted_abs_path_win" in evt for evt in out["audit"])


def test_pii_redact_unix_home_path(tmp_path: Path):
    payload = {
        "name": "save-from-home-unix",
        "description": "Persist a file out of the user home tree.",
        "procedure": (
            "1. Read source from /home/keane/projects/notes.md.\n"
            "2. Convert to docx.\n3. Save under outputs/.\n"
            "4. Verify file exists."
        ),
        "tags": ["filesystem"],
    }
    distiller, _, _, _ = _make_distiller(
        tmp_path, sc_seed=10, chat_payload=payload)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out["kind"] == "create_skill"
    assert "/home/keane" not in out["procedure"]
    assert "<USER_HOME>" in out["procedure"]


# ===========================================================================
# (3) Layer-2 PII gate — public scan_text wrapper
# ===========================================================================

def test_scan_text_public_wrapper_blocks_email(tmp_path: Path):
    distiller, _, _, _ = _make_distiller(tmp_path)
    allow, cleaned, audit = distiller.scan_text(
        "Email the rep at jane.doe@example.com after merging.")
    assert allow is False
    assert any("pii_email" in evt for evt in audit)


def test_scan_text_public_wrapper_redacts_path(tmp_path: Path):
    distiller, _, _, _ = _make_distiller(tmp_path)
    allow, cleaned, audit = distiller.scan_text(
        "Copy from C:\\Users\\Bob\\Docs\\plan.txt to outputs/")
    assert allow is True
    assert "Bob" not in cleaned
    assert "<USER_HOME>" in cleaned


# ===========================================================================
# (4) LLM failure modes
# ===========================================================================

def test_llm_exception_returns_none(tmp_path: Path):
    """LLM raising → silent skip (not a crash). The Distiller is a
    'nice to have' — never load-bearing."""
    distiller, _, _, _ = _make_distiller(
        tmp_path, sc_seed=10, llm_raises=True)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is None


def test_llm_empty_payload_returns_none(tmp_path: Path):
    """If the LLM returns an empty/unparseable draft, drop silently."""
    distiller, _, _, _ = _make_distiller(
        tmp_path, sc_seed=10,
        chat_payload={"name": "", "description": "", "procedure": ""})
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is None


# ===========================================================================
# (5) Phase 2 — abstraction pass (Principle + Parameters)
# ===========================================================================
#
# The abstraction pass runs a SECOND LLM (default GPT-4o-mini via OpenAI)
# after the raw draft, lifting it into a tool/language-agnostic principle
# plus a parameters JSON. It is best-effort and failure-isolated: any
# error / missing key / "off" switch must still ship the proposal with
# the raw procedure (Phase-1A behaviour), just with empty principle.
#
# We monkeypatch `openai_chat_json` on the provider module (the Distiller
# imports it lazily from there) so these tests never touch the network.

import teow_agl.adapters.openai_provider as _oai  # noqa: E402


def _patch_abstraction(monkeypatch, *, returns=None, raises=False,
                       key="sk-test-key", provider="openai"):
    """Wire env + a fake openai_chat_json for the abstraction pass.

    `returns` is the dict the fake LLM yields; `raises=True` makes it
    blow up (to exercise the failure-isolation path). Tracks call count
    on the returned list so a test can assert the LLM was/wasn't reached.
    """
    if provider is None:
        monkeypatch.delenv("SKILL_ABSTRACTION_LLM", raising=False)
    else:
        monkeypatch.setenv("SKILL_ABSTRACTION_LLM", provider)
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)

    calls: list[dict] = []

    def _fake(*, system, user, max_tokens=400, temperature=0.1,
              **kwargs):
        calls.append({"system": system, "user": user})
        if raises:
            raise RuntimeError("simulated abstraction error")
        return returns if returns is not None else {}

    monkeypatch.setattr(_oai, "openai_chat_json", _fake)
    monkeypatch.setattr(_oai, "_resolve_chat_model", lambda explicit=None:
                        "gpt-4o-mini")
    return calls


def test_abstraction_adds_principle_and_parameters(tmp_path, monkeypatch):
    """Happy path: the abstraction LLM returns a principle + parameters,
    both land on the proposal and the model provenance is recorded."""
    calls = _patch_abstraction(monkeypatch, returns={
        "principle": ("Persist generated text into a portable document "
                      "format and verify the artifact exists."),
        "parameters": {"tool": "docx", "output_format": "docx",
                       "output_language": "en"},
    })
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None and out["kind"] == "create_skill"
    assert out["principle"].startswith("Persist generated text")
    assert out["parameters"] == {"tool": "docx", "output_format": "docx",
                                 "output_language": "en"}
    assert out["abstraction_model"] == "openai:gpt-4o-mini"
    assert len(calls) == 1  # abstraction LLM was actually reached


def test_abstraction_skipped_when_provider_off(tmp_path, monkeypatch):
    """SKILL_ABSTRACTION_LLM=none → no abstraction call, raw proposal
    still ships with empty principle/parameters."""
    calls = _patch_abstraction(monkeypatch, provider="none",
                               returns={"principle": "should not be used"})
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None and out["kind"] == "create_skill"
    assert out["principle"] == ""
    assert out["parameters"] == {}
    assert out["abstraction_model"] == "skipped:provider_off"
    assert calls == []  # LLM never reached


def test_abstraction_skipped_when_no_key(tmp_path, monkeypatch):
    """Provider on but OPENAI_API_KEY missing → skip, never call LLM."""
    calls = _patch_abstraction(monkeypatch, key=None,
                               returns={"principle": "unused"})
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None and out["kind"] == "create_skill"
    assert out["principle"] == ""
    assert out["abstraction_model"] == "skipped:no_key"
    assert calls == []


def test_abstraction_llm_failure_keeps_raw_proposal(tmp_path, monkeypatch):
    """The abstraction LLM raising must NOT sink the proposal — we still
    ship the raw procedure (failure isolation)."""
    _patch_abstraction(monkeypatch, raises=True)
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None and out["kind"] == "create_skill"
    assert out["principle"] == ""
    assert out["parameters"] == {}
    assert out["abstraction_model"] == "skipped:failed"


def test_abstraction_empty_response_keeps_raw_proposal(tmp_path, monkeypatch):
    """An empty {} from the LLM → skipped:empty, raw proposal still ships."""
    _patch_abstraction(monkeypatch, returns={})
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None and out["kind"] == "create_skill"
    assert out["principle"] == ""
    assert out["abstraction_model"] == "skipped:empty"


def test_abstraction_principle_truncated_to_50_words(tmp_path, monkeypatch):
    """An over-eager principle is hard-capped at 50 words and flattened to
    a single line (no newlines)."""
    long_principle = " ".join(f"word{i}" for i in range(80)) + "\nsecond line"
    _patch_abstraction(monkeypatch, returns={"principle": long_principle,
                                             "parameters": {}})
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out["kind"] == "create_skill"
    assert "\n" not in out["principle"]
    assert len(out["principle"].split()) == 50


def test_abstraction_principle_pii_hard_reject_blocks(tmp_path, monkeypatch):
    """A leaked email inside the principle trips the Layer-1 gate and
    blocks the whole proposal (field == principle)."""
    _patch_abstraction(monkeypatch, returns={
        "principle": "Always cc the lead at lead@example.com when done.",
        "parameters": {"tool": "docx"},
    })
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None and out["kind"] == "create_skill_blocked"
    assert out["field"] == "principle"
    assert any("pii_email" in evt for evt in out["audit"])


def test_abstraction_parameters_hard_reject_blocks(tmp_path, monkeypatch):
    """hard_reject PII inside a parameters value blocks the proposal
    (a JSON value can't carry a redaction placeholder)."""
    _patch_abstraction(monkeypatch, returns={
        "principle": "Generate a document and verify it.",
        "parameters": {"tool": "docx", "contact": "ops@example.com"},
    })
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None and out["kind"] == "create_skill_blocked"
    assert out["field"] == "parameters"
    assert any("pii_email" in evt for evt in out["audit"])


def test_abstraction_parameters_redact_hit_drops_params(tmp_path, monkeypatch):
    """A redactable value (user-home path) inside parameters → drop the
    WHOLE parameters dict but keep the proposal (defence in depth).

    Note: we use a unix-style /home/<user>/ path because the parameters
    are scanned via their JSON serialisation, and json.dumps doubles
    backslashes — which would defeat the Windows-path regex. Forward
    slashes survive serialisation, so the redact pattern fires."""
    _patch_abstraction(monkeypatch, returns={
        "principle": "Generate a document from a source file and verify it.",
        "parameters": {"tool": "docx",
                       "source": "/home/alice/projects/in.txt"},
    })
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out is not None and out["kind"] == "create_skill"
    assert out["parameters"] == {}  # dropped, not redacted-in-place
    assert out["principle"].startswith("Generate a document")
    assert any("redacted_abs_path_unix" in evt for evt in out["audit"])


def test_abstraction_non_dict_parameters_coerced_empty(tmp_path, monkeypatch):
    """If the LLM returns a non-dict `parameters`, we coerce it to {} and
    still ship the proposal with the principle."""
    _patch_abstraction(monkeypatch, returns={
        "principle": "Generate and verify a document artifact.",
        "parameters": ["not", "a", "dict"],
    })
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    out = distiller.maybe_propose(task_result=_task_result())
    assert out["kind"] == "create_skill"
    assert out["parameters"] == {}
    assert out["principle"].startswith("Generate and verify")


def test_load_abstraction_prompt_falls_back_inline(tmp_path, monkeypatch):
    """A missing/absent prompt file falls back to the inline prompt — the
    Distiller is never blocked by a bad path."""
    from teow_agl.modules.module_109b_skill_distiller import (
        _INLINE_ABSTRACTION_PROMPT,
    )
    distiller, _, _, _ = _make_distiller(tmp_path, sc_seed=10)
    # _make_distiller passes no abstraction_prompt_path → None → inline
    assert distiller._load_abstraction_prompt() == _INLINE_ABSTRACTION_PROMPT


def test_load_abstraction_prompt_reads_file(tmp_path, monkeypatch):
    """When a real prompt file is configured it is read and cached."""
    p = tmp_path / "abstraction_prompt.md"
    p.write_text("CUSTOM ABSTRACTION SYSTEM PROMPT", encoding="utf-8")
    sc = SubjectConfidence(tmp_path / "sc.jsonl")
    _seed_successes(sc, "office_doc", 10)
    sm = SkillManager(tmp_path / "skills", constraints=_constraints())
    distiller = SkillDistiller(
        chat_llm=FakeChatLLM(), skill_manager=sm,
        subject_confidence=sc, constraints=_constraints(),
        abstraction_prompt_path=p,
    )
    assert distiller._load_abstraction_prompt() == (
        "CUSTOM ABSTRACTION SYSTEM PROMPT")
    # Mutate the file — cached value must persist (no re-read)
    p.write_text("CHANGED", encoding="utf-8")
    assert distiller._load_abstraction_prompt() == (
        "CUSTOM ABSTRACTION SYSTEM PROMPT")
