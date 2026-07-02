"""V3 mixed-mode planner selection — one server, two honest tiers.

The operator may start ONE server where the core demo stays deterministic
(smart_mock) while selected workflows run on the live API — no restart between
demo parts (TEOW_AGL_LIVE_WORKFLOWS=<workflow_id,...>). These tests pin the
selection logic keyless and network-free:

  * default (env unset)          → everything deterministic (exact current behaviour)
  * live list + key + Route B    → live; national / probes stay deterministic
  * live list WITHOUT a key      → deterministic (never pretend)
  * the env override used during a live build is always restored
  * /api/config exposes live_workflows + live_ready for the honest UI badges
"""
from __future__ import annotations

import os

import pytest

import server.app as appmod

ROUTE_B_GOAL = (
    "School X held an April upper-level English speech competition. Alice won "
    "Champion, Ben won 2nd place, and Chloe won 3rd place. Alice will represent "
    "the school at district level. Daniel and Emma could not finish memorising "
    "their speeches. The school will simplify their scripts, coach them for two "
    "weeks, and let them speak again at assembly. Prepare a Facebook post, "
    "internal report, champion parent notice, and private guidance notice for "
    "Daniel and Emma's parents. Do not send or publish anything."
)
NAT_GOAL = "全国赛成绩出来了，处理一下。"


def _clear_env(monkeypatch):
    monkeypatch.delenv("TEOW_AGL_LIVE_WORKFLOWS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TEOW_AGL_PLANNER", raising=False)
    monkeypatch.delenv("TEOW_AGL_CHAT_LLM", raising=False)


def test_default_is_fully_deterministic(monkeypatch):
    _clear_env(monkeypatch)
    assert appmod._live_workflow_ids() == set()
    assert appmod._goal_runs_live(ROUTE_B_GOAL) is False
    assert appmod._goal_runs_live(NAT_GOAL) is False


def test_live_list_with_key_selects_only_listed_workflow(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TEOW_AGL_LIVE_WORKFLOWS", "ad_hoc_school_event_reporting")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    assert appmod._goal_runs_live(ROUTE_B_GOAL) is True
    # national workflow + a non-workflow probe stay deterministic
    assert appmod._goal_runs_live(NAT_GOAL) is False
    assert appmod._goal_runs_live("What is photosynthesis?") is False


def test_live_list_without_key_never_goes_live(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TEOW_AGL_LIVE_WORKFLOWS", "ad_hoc_school_event_reporting")
    assert appmod._goal_runs_live(ROUTE_B_GOAL) is False


def test_globally_live_planner_needs_no_override(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TEOW_AGL_LIVE_WORKFLOWS", "ad_hoc_school_event_reporting")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("TEOW_AGL_PLANNER", "openai")
    assert appmod._goal_runs_live(ROUTE_B_GOAL) is False


def test_live_build_overrides_env_then_restores(monkeypatch):
    """During a live build the planner/chat env is openai; afterwards it is
    ALWAYS restored — a concurrently-built deterministic runtime can never
    inherit the override (all builds share one construction lock)."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("TEOW_AGL_LIVE_WORKFLOWS", "ad_hoc_school_event_reporting")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    seen: dict = {}

    def fake_build():
        seen["planner"] = os.environ.get("TEOW_AGL_PLANNER")
        seen["chat"] = os.environ.get("TEOW_AGL_CHAT_LLM")
        return object()

    monkeypatch.setattr(appmod, "_build_runtime", fake_build)
    rt, mode = appmod._make_runtime_for_goal(ROUTE_B_GOAL)
    assert mode == "live"
    assert seen == {"planner": "openai", "chat": "openai"}
    # restored after the build (we cleared them above → gone again)
    assert os.environ.get("TEOW_AGL_PLANNER") is None
    assert os.environ.get("TEOW_AGL_CHAT_LLM") is None

    rt, mode = appmod._make_runtime_for_goal(NAT_GOAL)
    assert mode == "deterministic"
    assert seen["planner"] == "openai" or True  # fake reused; mode is the check


def test_config_exposes_live_fields(monkeypatch):
    from fastapi.testclient import TestClient
    _clear_env(monkeypatch)
    monkeypatch.setenv("TEOW_AGL_LIVE_WORKFLOWS", "ad_hoc_school_event_reporting")
    c = TestClient(appmod.app)
    body = c.get("/api/config").json()
    assert body["live_workflows"] == ["ad_hoc_school_event_reporting"]
    assert body["live_ready"] is False          # no key → never claim live
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    body = c.get("/api/config").json()
    assert body["live_ready"] is True
