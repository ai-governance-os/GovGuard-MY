"""Module 102B — Phase 2 L4.5 cross-context skill adaptation unit tests.

These exercise the synthesizer's `_adapt_skill_to_task` contract in
isolation (no Runtime, no network):

  * body parsing (3-section + legacy single-section)
  * format-validity sanity check (tool-specific structural cues)
  * the adaptation decision tree:
      - env kill-switch / unknown provider / missing key
      - happy path (mocked strong LLM returns a well-shaped procedure)
      - empty / CANNOT_ADAPT / wrong-format / LLM-raises failure modes
      - code-fence stripping
      - body fallback when explicit principle/procedure keys are absent
  * inline-prompt fallback when no prompt file is configured

The OpenAI call is fully mocked — these tests never spend a token.
"""
from __future__ import annotations

import pytest

import teow_agl.adapters.openai_provider as _oai
from teow_agl.modules.module_102b_synthesizer import (
    ContentSynthesizer,
    _INLINE_ADAPTATION_PROMPT,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
_THREE_SECTION_BODY = """# save-q3-docx-report

_Write a quarterly report as a Word document._

<!-- skill_id: skill_abc123def456 | task_id: t1 | created: 2026-05-29 -->

## Principle

Organise findings into a structured narrative and verify the artifact.

## Parameters

```json
{
  "tool": "docx",
  "output_format": "docx",
  "output_language": "en"
}
```

## Procedure

1. Draft an outline of the report's sections.
2. Write each section as full prose paragraphs.
3. Save as a Word document under outputs/.
4. Verify the file exists and is non-empty.
"""

_LEGACY_BODY = """# legacy-skill

_An old skill with no abstraction sections._

<!-- skill_id: skill_legacy000001 | task_id: t0 | created: 2026-01-01 -->

1. Do the first thing.
2. Do the second thing.
3. Verify it worked.
4. Return the path.
"""

_GOOD_PPTX_PROCEDURE = (
    "1. Break the report's sections into individual slides.\n"
    "2. Give each slide a short title and 3-5 concise bullet points.\n"
    "3. Add a closing slide summarising the key takeaways.\n"
    "4. Save the slide deck and verify it opens."
)


def _patch_adaptation(monkeypatch, *, returns=None, raises=False,
                      key="sk-test-key", provider="openai"):
    """Wire env + a fake openai_chat for the adaptation pass."""
    if provider is None:
        monkeypatch.delenv("SKILL_ADAPTATION_LLM", raising=False)
    else:
        monkeypatch.setenv("SKILL_ADAPTATION_LLM", provider)
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)

    calls: list[dict] = []

    def _fake(*, system, user, max_tokens=2000, temperature=0.2, **kwargs):
        calls.append({"system": system, "user": user})
        if raises:
            raise RuntimeError("simulated adaptation error")
        return returns if returns is not None else ""

    monkeypatch.setattr(_oai, "openai_chat", _fake)
    return calls


class _NullChat:
    backend = "none"


def _synth() -> ContentSynthesizer:
    # No prompt path → inline fallback. chat_llm is irrelevant to
    # adaptation (it uses the OpenAI sidecar).
    return ContentSynthesizer(chat_llm=_NullChat())


def _docx_skill() -> dict:
    return {"skill_id": "skill_abc123def456",
            "name": "save-q3-docx-report",
            "description": "Write a quarterly report as a Word document.",
            "body": _THREE_SECTION_BODY}


# ===========================================================================
# (1) body parsing
# ===========================================================================

def test_parse_three_section_body():
    parsed = ContentSynthesizer._parse_skill_body(_THREE_SECTION_BODY)
    assert parsed["principle"].startswith("Organise findings")
    assert parsed["parameters"] == {"tool": "docx", "output_format": "docx",
                                    "output_language": "en"}
    assert "Draft an outline" in parsed["procedure"]
    # Principle / Parameters headings must NOT bleed into procedure
    assert "## Principle" not in parsed["procedure"]


def test_parse_legacy_body_uses_post_comment_text():
    parsed = ContentSynthesizer._parse_skill_body(_LEGACY_BODY)
    assert parsed["principle"] == ""
    assert parsed["parameters"] == {}
    assert "Do the first thing." in parsed["procedure"]
    # The metadata comment itself must be gone
    assert "skill_id:" not in parsed["procedure"]


def test_parse_empty_body():
    parsed = ContentSynthesizer._parse_skill_body("")
    assert parsed == {"principle": "", "parameters": {}, "procedure": ""}


# ===========================================================================
# (2) format-validity sanity check
# ===========================================================================

def test_format_valid_pptx_requires_slide_cue():
    s = _synth()
    assert s._adapted_format_valid(_GOOD_PPTX_PROCEDURE, "pptx") is True
    # Same length + step count but no slide/bullet language → invalid
    no_cue = ("1. Write the first paragraph.\n"
              "2. Write the second paragraph.\n"
              "3. Save it as a document file.")
    assert s._adapted_format_valid(no_cue, "pptx") is False


def test_format_valid_rejects_too_short_or_too_few_steps():
    s = _synth()
    assert s._adapted_format_valid("1. slide bullet", "pptx") is False  # short
    long_one_step = "1. " + "slide bullet " * 20
    assert s._adapted_format_valid(long_one_step, "pptx") is False  # 1 step


def test_format_valid_unknown_tool_passes_on_length():
    s = _synth()
    text = ("1. Do a meaningful first step here.\n"
            "2. Do a meaningful second step here.\n"
            "3. Verify the result is correct.")
    assert s._adapted_format_valid(text, "mystery_tool") is True


# ===========================================================================
# (3) adaptation decision tree
# ===========================================================================

def test_adapt_disabled_via_env(monkeypatch):
    _patch_adaptation(monkeypatch, provider="none",
                      returns=_GOOD_PPTX_PROCEDURE)
    adapted, status = _synth()._adapt_skill_to_task(
        _docx_skill(), target_tool="pptx",
        target_intent="Make a Q3 board deck")
    assert adapted is None
    assert status == "adaptation_disabled"


def test_adapt_unknown_provider(monkeypatch):
    _patch_adaptation(monkeypatch, provider="claude")
    adapted, status = _synth()._adapt_skill_to_task(
        _docx_skill(), target_tool="pptx")
    assert adapted is None
    assert status.startswith("adaptation_unknown_provider")


def test_adapt_no_key(monkeypatch):
    _patch_adaptation(monkeypatch, key=None,
                      returns=_GOOD_PPTX_PROCEDURE)
    adapted, status = _synth()._adapt_skill_to_task(
        _docx_skill(), target_tool="pptx")
    assert adapted is None
    assert status == "adaptation_no_key"


def test_adapt_happy_path_pptx(monkeypatch):
    calls = _patch_adaptation(monkeypatch, returns=_GOOD_PPTX_PROCEDURE)
    adapted, status = _synth()._adapt_skill_to_task(
        _docx_skill(), target_tool="pptx",
        target_intent="Make a Q3 board deck", target_format="slide_deck")
    assert status == "ok"
    assert adapted is not None
    assert "slide" in adapted.lower()
    assert len(calls) == 1
    # The skill's principle + procedure were handed to the LLM
    user_msg = calls[0]["user"]
    assert "Organise findings" in user_msg
    assert "Draft an outline" in user_msg
    assert "pptx" in user_msg


def test_adapt_empty_response(monkeypatch):
    _patch_adaptation(monkeypatch, returns="   ")
    adapted, status = _synth()._adapt_skill_to_task(
        _docx_skill(), target_tool="pptx")
    assert adapted is None
    assert status == "adaptation_empty"


def test_adapt_cannot_adapt_token(monkeypatch):
    _patch_adaptation(monkeypatch, returns="CANNOT_ADAPT")
    adapted, status = _synth()._adapt_skill_to_task(
        _docx_skill(), target_tool="pptx")
    assert adapted is None
    assert status == "adaptation_declined"


def test_adapt_invalid_format(monkeypatch):
    """A pptx target but a procedure with no slide/bullet language → the
    sanity check rejects it (this is what forces the fallback to raw)."""
    docx_shaped = ("1. Write the introduction paragraph.\n"
                   "2. Expand each point into prose.\n"
                   "3. Save it as a file and verify.")
    _patch_adaptation(monkeypatch, returns=docx_shaped)
    adapted, status = _synth()._adapt_skill_to_task(
        _docx_skill(), target_tool="pptx")
    assert adapted is None
    assert status == "adaptation_invalid_format"


def test_adapt_llm_raises_is_isolated(monkeypatch):
    _patch_adaptation(monkeypatch, raises=True)
    adapted, status = _synth()._adapt_skill_to_task(
        _docx_skill(), target_tool="pptx")
    assert adapted is None
    assert status.startswith("adaptation_error")


def test_adapt_no_procedure(monkeypatch):
    _patch_adaptation(monkeypatch, returns=_GOOD_PPTX_PROCEDURE)
    # Body parses to an empty procedure (no headings, no post-comment
    # text) → nothing to adapt.
    empty_skill = {"skill_id": "skill_000000000000", "name": "x",
                   "description": "y", "body": "   "}
    adapted, status = _synth()._adapt_skill_to_task(
        empty_skill, target_tool="pptx")
    assert adapted is None
    assert status == "adaptation_no_procedure"


def test_adapt_strips_code_fence(monkeypatch):
    fenced = "```\n" + _GOOD_PPTX_PROCEDURE + "\n```"
    _patch_adaptation(monkeypatch, returns=fenced)
    adapted, status = _synth()._adapt_skill_to_task(
        _docx_skill(), target_tool="pptx")
    assert status == "ok"
    assert not adapted.startswith("```")
    assert "slide" in adapted.lower()


def test_adapt_uses_explicit_keys_over_body(monkeypatch):
    """When the skill dict carries explicit principle/procedure keys we
    use those directly instead of parsing the markdown body."""
    calls = _patch_adaptation(monkeypatch, returns=_GOOD_PPTX_PROCEDURE)
    skill = {"skill_id": "skill_explicit0001",
             "principle": "EXPLICIT_PRINCIPLE_TOKEN survives transfer.",
             "parameters": {"tool": "docx"},
             "procedure": "1. EXPLICIT_PROCEDURE_TOKEN step.\n2. done."}
    adapted, status = _synth()._adapt_skill_to_task(
        skill, target_tool="pptx", target_intent="deck")
    assert status == "ok"
    user_msg = calls[0]["user"]
    assert "EXPLICIT_PRINCIPLE_TOKEN" in user_msg
    assert "EXPLICIT_PROCEDURE_TOKEN" in user_msg


# ===========================================================================
# (4) prompt loading
# ===========================================================================

def test_load_adaptation_prompt_falls_back_inline():
    s = _synth()  # no adaptation_prompt_path
    assert s._load_adaptation_prompt() == _INLINE_ADAPTATION_PROMPT


def test_load_adaptation_prompt_reads_and_caches(tmp_path):
    p = tmp_path / "adapt.md"
    p.write_text("CUSTOM ADAPTATION PROMPT", encoding="utf-8")
    s = ContentSynthesizer(chat_llm=_NullChat(), adaptation_prompt_path=p)
    assert s._load_adaptation_prompt() == "CUSTOM ADAPTATION PROMPT"
    p.write_text("CHANGED", encoding="utf-8")
    assert s._load_adaptation_prompt() == "CUSTOM ADAPTATION PROMPT"
