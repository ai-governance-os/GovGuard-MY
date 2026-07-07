"""GovGuard MY web server — Powered by TEOW-AGL Governance Runtime."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from teow_agl.adapters.chat_llm import ChatLLM
from teow_agl.adapters.smart_mock_planner import SmartMockPlanner
from teow_agl.models import TaskEnvelope
from teow_agl.modules.module_102b_synthesizer import ContentSynthesizer
from teow_agl.modules.module_102w_workflow_resolver import WorkflowResolver
from teow_agl.modules.module_102t_task_tree import TaskTreeModule
from teow_agl.modules.module_105_web_gate import WebHumanGate
from teow_agl.modules.module_109_reflector import ReflectorModule
from teow_agl.modules.module_curator import CuratorModule
from teow_agl.runtime import Runtime
from teow_agl.tools.chat_tool import ChatTool
from teow_agl.tools.desktop_tools import DesktopTool, get_desktop_path
from teow_agl.tools.filesystem_tools import FilesystemTool
from teow_agl.tools.gui_tools import GuiTool
from teow_agl.tools.image_tool import ImageGenTool
from teow_agl.tools.mock_tools import MockTool
from teow_agl.tools.office_tools import DocxTool, PptxTool, XlsxTool
from teow_agl.tools.report_tools import ReportTool
from teow_agl.tools.session_search_tool import SessionSearchTool
from teow_agl.tools.skill_tool import SkillTool
from teow_agl.tools.web_search_tool import WebSearchTool

from .persistence import JsonlStore


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "configs"
PROMPTS_DIR = ROOT / "prompts"
STATIC_DIR = ROOT / "static"
TRACE_DIR = ROOT / "traces"
OUTPUTS_DIR = ROOT / "outputs"
WORKSPACE_DIR = ROOT / "workspace"
STATE_DIR = ROOT / "state"
RAG_INDEX_PATH = STATE_DIR / "rag" / "index.jsonl"
SUBJECT_CONF_PATH = STATE_DIR / "subject_confidence.jsonl"
PLAN_CACHE_PATH = STATE_DIR / "plan_cache.jsonl"
USER_MEMORY_DIR = STATE_DIR / "memory"
SKILLS_DIR = STATE_DIR / "skills"
SESSION_INDEX_PATH = STATE_DIR / "session_index.db"
DEMO_DIR = ROOT / "demo"


def _seed_demo_results() -> None:
    """Seed the sample (public-safe) results files into the workspace so a
    workflow's first step (extract results) reads real data instead of showing
    `not_found` in a fresh clone. Demo convenience only — never overwrites a
    real upload. Each workflow reads its own file via the step's `read_target`
    (post-event → results.md; national athletics → national_athletics_results.md;
    the rich student/parent database stays a backend source, never seeded here)."""
    seeds = {
        "sports_day_results.md": "results.md",
        "national_athletics_results.md": "national_athletics_results.md",
        "ad_hoc_school_event_results.md": "ad_hoc_school_event_results.md",
        "charity_bazaar_case.md": "charity_bazaar_case.md",
    }
    for src_name, dst_name in seeds.items():
        try:
            sample = DEMO_DIR / src_name
            target = WORKSPACE_DIR / dst_name
            if sample.exists() and not target.exists():
                WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
                target.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass


_seed_demo_results()


def _demo_mode() -> bool:
    """MAIC demo lockout (Owner Rule 4). Default ON for the judging build:
    external actions are simulated and clearly labelled; no real email /
    WhatsApp / API send / file deletion / external modification ever fires.
    Set MAIC_DEMO_MODE=0 to disable (not used during judging)."""
    return os.environ.get("MAIC_DEMO_MODE", "1").strip().lower() not in (
        "0", "false", "no", "off", ""
    )


def _planner_from_env():
    choice = os.environ.get("TEOW_AGL_PLANNER", "smart_mock").lower()
    if choice == "ollama":
        from teow_agl.adapters.ollama_provider import OllamaPlanner
        return OllamaPlanner(model=os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"))
    if choice == "groq":
        from teow_agl.adapters.groq_provider import GroqPlanner
        return GroqPlanner(model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    if choice == "gemini":
        from teow_agl.adapters.gemini_provider import GeminiPlanner
        return GeminiPlanner(model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))
    if choice == "openai":
        from teow_agl.adapters.openai_provider import OpenAIPlanner
        return OpenAIPlanner()  # model via OPENAI_PLANNER_MODEL / OPENAI_MODEL
    if choice == "anthropic":
        from teow_agl.adapters.anthropic_provider import AnthropicPlanner
        return AnthropicPlanner()  # model via ANTHROPIC_PLANNER_MODEL / ANTHROPIC_MODEL
    return SmartMockPlanner(default_outputs_dir=str(OUTPUTS_DIR))


def _workspace_roots() -> list[str]:
    # MAIC demo lockout (Owner Rule 4): in demo mode the agent is confined to
    # demo-safe folders only. The real Desktop is NEVER exposed — no path
    # leak in config/status, no implication of local-machine control.
    roots = [str(WORKSPACE_DIR), str(OUTPUTS_DIR)]
    if not _demo_mode():
        roots.append(str(get_desktop_path()))
    return roots


@dataclass
class TaskState:
    task_id: str
    raw_goal: str
    started_at: str
    finished_at: str | None = None
    status: str = "running"
    error: str | None = None
    events: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    executions: list[dict] = field(default_factory=list)
    pending_approvals: list[dict] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)
    final_route: str = ""
    # Mixed mode — which planner tier built this task's runtime
    # ("deterministic" | "live"). Honest per-task audit of the drafting tier;
    # governance is identical in both.
    planner_mode: str = "deterministic"
    # Module 109 reflection output for this task (None when reflector
    # was skipped / disabled). The UI reads this to render the
    # "What I learned" block and the REFLECT chip.
    reflection: dict | None = None
    # Module 110 verification output (None when verifier is disabled).
    # The UI reads this to show a VERIFIED chip on pass or a
    # VERIFY-FAIL chip with a tooltip on failure.
    verification: dict | None = None
    # Phase 13 — Task Tree (None when this task wasn't decomposed).
    # The UI renders this as a tree view of sub-goal leaves with their
    # statuses + summaries.
    task_tree: dict | None = None
    # Workflow Autonomy (102W/101D) — None unless a configured workflow was
    # detected OR the agent self-blocked a sensitive data use. The UI renders
    # this as a workflow panel beside the governance pipeline card.
    workflow: dict | None = None


_tasks_store = JsonlStore(STATE_DIR / "tasks.jsonl")
_proposals_store = JsonlStore(STATE_DIR / "proposals.jsonl")
_curator_store = JsonlStore(STATE_DIR / "curator_proposals.jsonl")


def _load_initial_state() -> tuple[dict, dict]:
    """Replay the JSONL stores so the UI keeps history across restarts."""
    tasks: dict[str, TaskState] = {}
    for rec in _tasks_store.replay():
        tid = rec.get("task_id")
        if not tid:
            continue
        # rebuild TaskState (skip events to keep startup fast; UI re-fetches if needed)
        tasks[tid] = TaskState(
            task_id=tid, raw_goal=rec.get("raw_goal", ""),
            started_at=rec.get("started_at", ""), finished_at=rec.get("finished_at"),
            status=rec.get("status", "done"), error=rec.get("error"),
            events=rec.get("events", []), decisions=rec.get("decisions", []),
            executions=rec.get("executions", []), pending_approvals=[],
            proposals=rec.get("proposals", []), final_route=rec.get("final_route", ""),
            reflection=rec.get("reflection"),
            verification=rec.get("verification"),
            task_tree=rec.get("task_tree"),
            workflow=rec.get("workflow"),
        )
    proposals: dict[str, dict] = _proposals_store.latest_by_id("patch_id")
    return tasks, proposals


def _load_curator_state() -> dict[str, dict]:
    """Replay the curator proposals jsonl. Latest record per proposal_id wins."""
    return _curator_store.latest_by_id("proposal_id")


_initial_tasks, _initial_proposals = _load_initial_state()
_initial_curator = _load_curator_state()
_app_state = {
    "tasks": _initial_tasks,
    "proposals": _initial_proposals,
    # Phase 16 curator proposals — keyed by proposal_id. Replayed from
    # state/curator_proposals.jsonl on startup so approvals survive
    # server restarts (same pattern as 104 patches).
    "curator_proposals": _initial_curator,
    "lock": threading.Lock(),
    "gate": WebHumanGate(),
}


def _build_runtime() -> Runtime:
    # Construction body. Call via _make_runtime() / _make_runtime_for_goal()
    # so every build is serialised under _RUNTIME_BUILD_LOCK (mixed mode
    # temporarily overrides planner env during a live build).
    workspace_roots = _workspace_roots()
    # Shared chat-LLM adapter for ChatTool's synth fallback AND Module 102B
    # content synthesis. Backend selection mirrors the planner env var so
    # one credential gets the whole pipeline talking.
    chat_llm = ChatLLM()
    tools = {
        "fs": FilesystemTool(workspace_roots),
        "report": ReportTool(),
        "docx": DocxTool(workspace_roots),
        "pptx": PptxTool(workspace_roots),
        "xlsx": XlsxTool(workspace_roots),
        "desktop": DesktopTool(workspace_roots),
        "gui": GuiTool(screenshots_dir=OUTPUTS_DIR / "_screenshots"),
        "image_gen": ImageGenTool(
            images_dir=OUTPUTS_DIR / "_images",
            workspace_roots=[str(OUTPUTS_DIR)],
        ),
        "chat": ChatTool(synth=lambda user_intent: chat_llm.chat(
            system=(
                "You are a helpful, concise assistant. Answer the user's "
                "question directly in natural language, in the user's own "
                "language. No JSON, no preamble like 'Here is the answer:'."
            ),
            user=user_intent,
            max_tokens=1200,
        )),
        "web_search": WebSearchTool(),
        "email": MockTool("email"), "publish": MockTool("publish"),
        "code": MockTool("code"), "shell": MockTool("shell"),
        # `human` MockTool intentionally NOT registered — see
        # configs/tool_catalog.json _human_DEPRECATED for why.
        # If the planner ever picks tool='human' it will now hit
        # 'no_tool_handler' and route to a proper failure event, not
        # silently emit the literal 'mock_request_clarification'.
    }
    if _demo_mode():
        # MAIC demo lockout: stub anything implying real desktop / GUI /
        # local-machine control. The governance/approval/ticket/audit path is
        # unchanged; only the side-effecting backends become simulated mocks.
        tools["desktop"] = MockTool("desktop")
        tools["gui"] = MockTool("gui")
    # Module 109 Reflector — uses the same chat LLM. Its constraints
    # are loaded by Runtime from configs/reflection_constraints.json so
    # we just hand the LLM and let Runtime feed the constraints dict.
    # (We construct it AFTER Runtime so we can pass the constraints
    # dict it just read.) See the two-step dance below.
    # Flagship domain pack. Defaults to public_school (the MAIC flagship);
    # swap any pack by config alone via TEOW_AGL_DOMAIN_PACK, no code change.
    domain = os.environ.get("TEOW_AGL_DOMAIN_PACK", "public_school")
    rt = Runtime(
        config_dir=CONFIG_DIR, prompts_dir=PROMPTS_DIR,
        planner=_planner_from_env(), tool_registry=tools,
        human_gate=_app_state["gate"], trace_dir=TRACE_DIR,
        rag_index_path=RAG_INDEX_PATH if RAG_INDEX_PATH.exists() else None,
        subject_confidence_path=SUBJECT_CONF_PATH,
        plan_cache_path=PLAN_CACHE_PATH,
        user_memory_dir=USER_MEMORY_DIR,
        plan_cache_outputs_dir=str(OUTPUTS_DIR),
        content_synthesizer=ContentSynthesizer(chat_llm=chat_llm),
        skill_manager_dir=SKILLS_DIR,
        session_index_path=SESSION_INDEX_PATH,
        domain_pack=domain,
    )
    # Now attach the reflector. Runtime has already loaded
    # reflection_constraints.json into self.reflection_constraints — we
    # just hand the module a reference + the shared chat LLM.
    rt.reflector = ReflectorModule(
        chat_llm=chat_llm,
        constraints=rt.reflection_constraints,
    )
    # Phase 14 — give the existing VerifierModule a chat LLM so its
    # llm_judge() method can actually call out. Without this the judge
    # always short-circuits to skipped:no_chat_llm — which is the right
    # behaviour in tests / offline mode.
    if rt.verifier is not None:
        rt.verifier.chat_llm = chat_llm
    # Phase 16 — Curator. Reuses the shared chat LLM. Optional: when
    # config disabled / file missing, runtime.curator stays None and
    # the API endpoint returns a "not attached" error.
    rt.curator = CuratorModule(
        chat_llm=chat_llm,
        config=rt.curator_rules,
    )
    # Phase 13 — Task Tree Planner (Module 102T). Decomposer system
    # prompt + heuristics live alongside the planner prompt. Shares the
    # same chat LLM so the user only configures one credential.
    try:
        decomposer_prompt = (PROMPTS_DIR / "module_102t_decomposer_system.md").read_text(encoding="utf-8")
        rt.task_tree = TaskTreeModule(
            chat_llm=chat_llm,
            system_prompt=decomposer_prompt,
            config=rt.task_decomposition_cfg,
        )
    except Exception:
        # Missing prompt file / config → leave task_tree unset; runtime
        # silently falls back to single-shot for every task.
        rt.task_tree = None
    # Now that the runtime has a UserMemory, register the memory tool that
    # routes LLM calls into it.
    if rt.user_memory is not None:
        from teow_agl.tools.memory_tool import MemoryTool
        tools["memory"] = MemoryTool(rt.user_memory)
    # Same dance for the SkillManager — the tool is bound to the runtime-
    # owned instance so the LLM and runtime share one source of truth.
    if rt.skill_manager is not None:
        tools["skill_manager"] = SkillTool(rt.skill_manager)
    # Phase 17 — session_search tool reads from the runtime-owned
    # FTS5 index. Skipped when FTS5 isn't available (very rare cpython
    # builds without it) — the tool registry simply lacks the entry.
    if rt.session_index is not None and rt.session_index.available:
        tools["session_search"] = SessionSearchTool(rt.session_index)
    rt.profile.profile["workspace_roots"] = workspace_roots
    return rt


# ---------------------------------------------------------------------------
# Mixed-mode planner selection. ONE server can run the core demo
# deterministically (smart_mock) while selected workflows run on the live API
# — no restart between demo parts. Opt in at startup, e.g.:
#     TEOW_AGL_LIVE_WORKFLOWS=ad_hoc_school_event_reporting
# (comma-separated workflow_ids; add school_charity_bazaar to take Route A
# live too). A task is built live ONLY when (a) its goal pre-resolves to a
# listed workflow, (b) an OPENAI_API_KEY is present, and (c) the global
# planner is not already live. The keyless default (env unset) is EXACTLY
# the current single-mode behaviour. Governance, routing, validators and the
# curated-fallback net are planner-independent — the live model only drafts
# prose; it never becomes the safety authority.
# ---------------------------------------------------------------------------
_RUNTIME_BUILD_LOCK = threading.Lock()
_WF_PRERESOLVER: WorkflowResolver | None = None


def _live_workflow_ids() -> set[str]:
    raw = os.environ.get("TEOW_AGL_LIVE_WORKFLOWS", "")
    return {w.strip() for w in raw.split(",") if w.strip()}


def _preresolve_workflow_id(raw_goal: str) -> str | None:
    """Cheap, deterministic 102W text-match on the goal BEFORE the runtime is
    built (the real resolution happens inside rt.run(); this mirrors it for
    planner selection only). Never raises."""
    global _WF_PRERESOLVER
    try:
        if _WF_PRERESOLVER is None:
            _WF_PRERESOLVER = WorkflowResolver(
                config_dir=CONFIG_DIR,
                domain=os.environ.get("TEOW_AGL_DOMAIN_PACK", "public_school"))
        env = TaskEnvelope(session_id="preresolve", user_id="preresolve",
                           raw_goal=raw_goal, normalized_goal=raw_goal,
                           metadata={})
        res = _WF_PRERESOLVER.resolve(env, None)
        return res.get("workflow_id") if res else None
    except Exception:
        return None


def _goal_runs_live(raw_goal: str) -> bool:
    live = _live_workflow_ids()
    if not live:
        return False
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return False  # never pretend: no key, no live tier
    if os.environ.get("TEOW_AGL_PLANNER", "smart_mock").lower() == "openai":
        return False  # whole server already live — no override needed
    return _preresolve_workflow_id(raw_goal) in live


def _make_runtime_for_goal(raw_goal: str) -> tuple[Runtime, str]:
    """Build the per-task runtime, upgrading planner + chat LLM to the live
    API when the goal resolves to a live-listed workflow. ALL construction is
    serialised under one lock so the temporary env override can never leak
    into a concurrently-built deterministic runtime; env is always restored."""
    live = _goal_runs_live(raw_goal)
    with _RUNTIME_BUILD_LOCK:
        if not live:
            return _build_runtime(), "deterministic"
        old = {k: os.environ.get(k)
               for k in ("TEOW_AGL_PLANNER", "TEOW_AGL_CHAT_LLM")}
        os.environ["TEOW_AGL_PLANNER"] = "openai"
        os.environ["TEOW_AGL_CHAT_LLM"] = "openai"
        try:
            rt = _build_runtime()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return rt, "live"


def _make_runtime() -> Runtime:
    """Lock-serialised deterministic-default runtime construction. Existing
    callers (curator endpoints, tests) keep this exact signature; the lock
    guarantees a concurrent live build's env override can never leak in."""
    with _RUNTIME_BUILD_LOCK:
        return _build_runtime()


app = FastAPI(title="GovGuard MY", version="10.7.5-MAIC")


# ---------------------------------------------------------------------------
# Auth (Phase C). Single-operator session auth, OFF by default for
# backwards compatibility. Set TEOW_AGL_AUTH_PASSWORD to enable: every
# /api/* route except /api/login and /api/health then requires a valid
# session (HttpOnly cookie set by POST /api/login, or the same token as
# an Authorization: Bearer header for scripts).
#
# Tokens are HMAC-signed with a per-process random secret — restarting
# the server invalidates all sessions (operator just logs in again).
# Static files stay open: the UI shell is not sensitive, all data flows
# through the protected API.
# ---------------------------------------------------------------------------
_AUTH_SECRET = secrets.token_hex(32)
_AUTH_COOKIE = "teow_session"
_AUTH_EXEMPT_PATHS = ("/api/login", "/api/health")
_SESSION_MAX_AGE = 12 * 3600


def _auth_password() -> str:
    return (os.environ.get("TEOW_AGL_AUTH_PASSWORD") or "").strip()


def _make_session_token() -> str:
    payload = secrets.token_hex(16)
    sig = hmac.new(_AUTH_SECRET.encode(), payload.encode(),
                   hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _token_valid(token: str) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.split(".", 1)
    want = hmac.new(_AUTH_SECRET.encode(), payload.encode(),
                    hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, want)


@app.middleware("http")
async def _auth_middleware(request, call_next):
    password = _auth_password()
    path = request.url.path
    if password and path.startswith("/api") \
            and path not in _AUTH_EXEMPT_PATHS:
        token = request.cookies.get(_AUTH_COOKIE) or ""
        if not token:
            header = request.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                token = header[7:].strip()
        if not _token_valid(token):
            return JSONResponse({"ok": False, "error": "auth_required"},
                                status_code=401)
    return await call_next(request)


class LoginRequest(BaseModel):
    password: str


@app.post("/api/login")
def login(req: LoginRequest):
    password = _auth_password()
    if not password:
        return {"ok": True, "auth": "disabled"}
    if not hmac.compare_digest((req.password or "").strip(), password):
        raise HTTPException(401, "wrong_password")
    token = _make_session_token()
    resp = JSONResponse({"ok": True, "token": token})
    resp.set_cookie(_AUTH_COOKIE, token, httponly=True, samesite="strict",
                    max_age=_SESSION_MAX_AGE)
    return resp


class StartTaskRequest(BaseModel):
    raw_goal: str
    backup_status: str | None = None


class DecideRequest(BaseModel):
    approval_id: str
    status: str
    note: str | None = None


@app.get("/api/health")
def health() -> dict:
    """Liveness + readiness. `ok` is True only when the critical checks
    (state/trace dirs writable) pass; budget status is informational."""
    checks: dict[str, bool] = {}
    for name, d in (("state_writable", STATE_DIR),
                    ("traces_writable", TRACE_DIR)):
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".health_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks[name] = True
        except OSError:
            checks[name] = False
    budget: dict = {}
    try:
        from teow_agl.policies.cost_guard import CostGuard
        cg_path = CONFIG_DIR / "cost_guard.json"
        if cg_path.exists():
            with cg_path.open("r", encoding="utf-8") as f:
                budget = CostGuard(
                    json.load(f), STATE_DIR / "cost_ledger.json",
                ).snapshot()
    except Exception:
        budget = {}
    return {
        "ok": all(checks.values()),
        "product": "GovGuard MY",
        "version": "10.7.5-MAIC",
        "runtime": "Powered by TEOW-AGL Governance Runtime",
        "checks": checks,
        "planner": os.environ.get("TEOW_AGL_PLANNER", "smart_mock"),
        "domain_pack": os.environ.get("TEOW_AGL_DOMAIN_PACK", "public_school"),
        "demo_mode": _demo_mode(),
        "budget": budget,
        "alerts_configured": bool(os.environ.get("ALERT_WEBHOOK_URL")),
    }


@app.get("/api/config")
def config_summary() -> dict:
    return {
        "planner": os.environ.get("TEOW_AGL_PLANNER", "smart_mock"),
        "domain_pack": os.environ.get("TEOW_AGL_DOMAIN_PACK", "public_school"),
        "demo_mode": _demo_mode(),
        # Mixed mode — which workflows are opted into the live tier, and
        # whether that tier can actually run (a key is present). The UI mode
        # badges read these so they NEVER claim "live" when it isn't.
        "live_workflows": sorted(_live_workflow_ids()),
        "live_ready": bool(
            _live_workflow_ids()
            and os.environ.get("OPENAI_API_KEY", "").strip()
        ),
        "workspace_roots": _workspace_roots(),
        # Desktop path is hidden in demo mode (no local-machine path leak).
        "desktop": ("(disabled in demo mode)" if _demo_mode()
                    else str(get_desktop_path())),
        "outputs": str(OUTPUTS_DIR), "config_dir": str(CONFIG_DIR),
    }


@app.post("/api/tasks")
def start_task(req: StartTaskRequest) -> dict:
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    state = TaskState(task_id=task_id, raw_goal=req.raw_goal,
                      started_at=datetime.now(timezone.utc).isoformat())
    with _app_state["lock"]:
        _app_state["tasks"][task_id] = state

    def runner():
        rt, planner_mode = _make_runtime_for_goal(req.raw_goal)
        with _app_state["lock"]:
            state.planner_mode = planner_mode
        original_emit = rt.trace.emit

        def capture_emit(*args, **kwargs):
            ev = original_emit(*args, **kwargs)
            with _app_state["lock"]:
                state.events.append(ev.model_dump())
            return ev
        rt.trace.emit = capture_emit  # type: ignore[method-assign]

        try:
            result = rt.run(raw_goal=req.raw_goal, backup_status=req.backup_status,
                            session_id=task_id, task_id=task_id)
            with _app_state["lock"]:
                state.decisions = [d.model_dump() for d in result.decisions]
                state.executions = [e.model_dump() for e in result.executions]
                proposals_payload = [p.model_dump() for p in result.proposals]
                state.proposals = proposals_payload
                state.final_route = result.final_route
                # Module 109 reflection (may be None when reflector was
                # disabled / skipped / no proposal). UI uses this to
                # show the REFLECT chip + "What I learned" block.
                state.reflection = result.reflection
                # Module 110 verification (None when verifier disabled).
                # UI uses this to show the VERIFIED / VERIFY-FAIL chip.
                state.verification = result.verification
                # Phase 13 — Task Tree (None when not decomposed).
                state.task_tree = (
                    result.task_tree.model_dump()
                    if result.task_tree is not None else None
                )
                # Workflow Autonomy panel (102W/101D). None for ordinary tasks.
                state.workflow = _workflow_view(result)
                state.status = "done"
                state.finished_at = datetime.now(timezone.utc).isoformat()
                state.pending_approvals = []
                # register every newly-proposed patch in the global registry
                for p in proposals_payload:
                    pid = p.get("patch_id")
                    if not pid:
                        continue
                    p_with_task = {**p, "task_id": task_id}
                    _app_state["proposals"][pid] = p_with_task
                    _proposals_store.append(p_with_task)
                # Phase 1A — Skill Distiller. The Distiller queues any
                # `create_skill` proposal onto rt.curator_proposals during
                # _after_run(). _make_runtime() builds a FRESH runtime per
                # task whose queue starts empty, so everything here was
                # produced by THIS run. Without this sync the proposal dies
                # with the discarded runtime — invisible to the UI and lost
                # on restart. Mirror the 104-patch pattern: push into the
                # shared curator queue + JSONL so /api/curator/proposals
                # lists it, /decide can approve it (Layer-2 PII gate +
                # SkillManager.create_skill), and it survives a restart.
                for sp in rt.curator_proposals:
                    spid = sp.get("proposal_id")
                    if not spid or spid in _app_state["curator_proposals"]:
                        continue
                    # Dedupe the deterministic workflow-SOP across runs/restarts.
                    # A fresh runtime per task can't see SOPs queued by a prior
                    # run, so the runtime's own per-run dedupe is not enough —
                    # guard HERE, where the persistent store lives. At most one
                    # undecided/applied SOP per workflow; otherwise every demo
                    # run of the same workflow would pile up identical proposals.
                    if sp.get("source_module") == "109B-SOP":
                        wfid = sp.get("source_workflow")
                        if any(e.get("source_module") == "109B-SOP"
                               and e.get("source_workflow") == wfid
                               and e.get("status") in ("pending", "approved", "applied")
                               for e in _app_state["curator_proposals"].values()):
                            continue
                    sp_with_task = {**sp, "task_id": task_id}
                    _app_state["curator_proposals"][spid] = sp_with_task
                    _curator_store.append(sp_with_task)
            # persist task summary
            _tasks_store.append(_state_to_dict(state))
        except Exception as exc:
            with _app_state["lock"]:
                state.status = "error"
                state.error = str(exc)
                state.finished_at = datetime.now(timezone.utc).isoformat()
            _tasks_store.append(_state_to_dict(state))
            # Phase C — operator alert on unhandled task failure.
            # No-op unless ALERT_WEBHOOK_URL is set; never raises.
            from teow_agl.util.alerts import send_alert
            send_alert(
                f"task failed: {exc}",
                details={"task_id": task_id,
                         "goal": (req.raw_goal or "")[:200]},
            )

    threading.Thread(target=runner, daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    with _app_state["lock"]:
        state = _app_state["tasks"].get(task_id)
        if state is None:
            raise HTTPException(404, "task_not_found")
        live_pending = _app_state["gate"].pending_snapshot()
        live_for_task = [a for a in live_pending if a.get("task_id") == task_id]
        if live_for_task and state.status not in ("done", "error"):
            state.pending_approvals = live_for_task
            state.status = "awaiting_approval"
            # The run is blocked at its human gate, so the final result isn't
            # built yet. Reconstruct the workflow panel from the trace so the
            # paused GREEN reads as "work done, awaiting verification", not
            # "asked before doing anything". Replaced by the full view on done.
            if state.workflow is None:
                state.workflow = _partial_workflow_view_from_events(state.events)
        elif not live_for_task and state.status == "awaiting_approval":
            state.pending_approvals = []
            state.status = "running"
        payload = _state_to_dict(state)
        # While blocked at the human gate the final result (incl. executions)
        # isn't assembled yet — but the BLUE draft steps have ALREADY executed
        # and their files are on disk. Surface them from the 107 trace events
        # so the operator can inspect the drafts BEFORE deciding: the approval
        # is for external release only, never for creating the drafts
        # (submission review, 2026-07-05). Replaced by the real execution list
        # when the run completes.
        if state.status == "awaiting_approval" and not state.executions:
            partial = [
                (ev.get("details") or {})
                for ev in (state.events or [])
                if ev.get("module") == "107"
                and ev.get("event_type") == "execution_completed"
                and (ev.get("details") or {}).get("status") == "success"
            ]
            if partial:
                payload["executions"] = partial
        return payload


@app.get("/api/tasks")
def list_tasks() -> dict:
    with _app_state["lock"]:
        return {"tasks": [
            {"task_id": s.task_id, "status": s.status, "raw_goal": s.raw_goal,
             "started_at": s.started_at, "final_route": s.final_route,
             "workflow_detected": bool(s.workflow and s.workflow.get("detected")),
             "workflow_summary": (s.workflow or {}).get("summary")}
            for s in _app_state["tasks"].values()
        ]}


@app.post("/api/tasks/{task_id}/decide")
def decide(task_id: str, req: DecideRequest) -> dict:
    if req.status not in ("approved", "rejected", "modified"):
        raise HTTPException(400, "invalid_status")
    ok = _app_state["gate"].decide(req.approval_id, req.status, req.note)
    if not ok:
        raise HTTPException(404, "approval_not_pending")
    return {"ok": True}


@app.get("/api/patches")
def list_patches(status: str | None = None) -> dict:
    """List all known policy-patch proposals, newest first.
    Optional ?status=proposed|approved|rejected filter."""
    with _app_state["lock"]:
        items = list(_app_state["proposals"].values())
    items.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    if status:
        items = [p for p in items if p.get("status") == status]
    return {"patches": items}


class PatchDecideRequest(BaseModel):
    status: str  # "approved" | "rejected"
    approved_by: str = "web_user"


@app.post("/api/patches/{patch_id}/decide")
def decide_patch(patch_id: str, req: PatchDecideRequest) -> dict:
    if req.status not in ("approved", "rejected"):
        raise HTTPException(400, "invalid_status")

    with _app_state["lock"]:
        patch = _app_state["proposals"].get(patch_id)
    if patch is None:
        raise HTTPException(404, "patch_not_found")
    if patch.get("status") != "proposed":
        raise HTTPException(409, f"patch_already_{patch.get('status')}")

    if req.status == "rejected":
        patch["status"] = "rejected"
        patch["approved_by"] = req.approved_by
        with _app_state["lock"]:
            _app_state["proposals"][patch_id] = patch
        _proposals_store.append(patch)
        return {"ok": True, "status": "rejected"}

    # Approved: build a fresh runtime to apply the patch (safe — apply path
    # only writes to learned_contextual_policy.json after the hard-safety
    # check inside contextual_policy.apply_approved_patch).
    rt = _make_runtime()
    from teow_agl.models import PolicyPatchProposal
    proposal = PolicyPatchProposal(**{k: v for k, v in patch.items() if k != "task_id"})
    applied, reason = rt.approve_and_apply_patch(proposal, approved_by=req.approved_by)
    if applied:
        patch["status"] = "applied"
    else:
        patch["status"] = "approved"  # approved but not applied (e.g. blocked by safety)
    patch["approved_by"] = req.approved_by
    patch["apply_reason"] = reason
    with _app_state["lock"]:
        _app_state["proposals"][patch_id] = patch
    _proposals_store.append(patch)
    return {"ok": True, "applied": applied, "reason": reason}


# ─── Phase 16 — Curator endpoints ───────────────────────────────────────
@app.post("/api/curator/run")
def curator_run() -> dict:
    """Trigger one curator pass. Returns the queued proposals (they
    also persist to state/curator_proposals.jsonl + the in-memory
    queue for the GET/decide endpoints to pick up)."""
    rt = _make_runtime()
    # Restore the global queue into THIS runtime so its run_curator()
    # appends to the same list the rest of the app sees.
    with _app_state["lock"]:
        rt.curator_proposals = list(_app_state["curator_proposals"].values())
    result = rt.run_curator()
    if not result.get("ok"):
        return result
    # Persist + sync into the shared state
    with _app_state["lock"]:
        for prop in result.get("proposals") or []:
            _app_state["curator_proposals"][prop["proposal_id"]] = prop
            _curator_store.append(prop)
    return result


@app.get("/api/curator/proposals")
def curator_list(status: str | None = None) -> dict:
    """List curator proposals (optionally filtered by status), newest first."""
    with _app_state["lock"]:
        items = list(_app_state["curator_proposals"].values())
    if status:
        items = [p for p in items if p.get("status") == status]
    items.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    return {"ok": True, "proposals": items, "count": len(items)}


class CuratorDecideRequest(BaseModel):
    status: str  # "approved" | "rejected"
    approved_by: str = "web_user"


@app.post("/api/curator/proposals/{proposal_id}/decide")
def curator_decide(proposal_id: str, req: CuratorDecideRequest) -> dict:
    """Human approve / reject a curator proposal. On approve, runtime
    applies it via UserMemory / SkillManager and the proposal status
    becomes `applied` (or stays `approved` if apply failed).

    For `kind=create_skill` (Module 109B Skill Distiller proposals),
    we re-run the PII gate HERE — between the human click and the
    SkillManager.create_skill call — so a proposal that was clean
    when queued can't be "approved through" a Layer-1 hit that
    the operator missed during review. This is Layer 2 of the
    double-gate; Layer 1 ran in the Distiller at draft time.
    """
    if req.status not in ("approved", "rejected"):
        raise HTTPException(400, "invalid_status")
    with _app_state["lock"]:
        prop = _app_state["curator_proposals"].get(proposal_id)
    if prop is None:
        raise HTTPException(404, "proposal_not_found")
    if prop.get("status") != "pending":
        raise HTTPException(409, f"already_{prop.get('status')}")

    # Build a fresh runtime to apply (same safety reasoning as the
    # 104 patch endpoint — apply touches UserMemory.replace() or
    # SkillManager.archive_skill(), both of which have their own
    # bounded-delta + threat-scan guards).
    rt = _make_runtime()
    # Seed THIS runtime's queue with all known proposals so the apply
    # path can find the one by id.
    with _app_state["lock"]:
        rt.curator_proposals = list(_app_state["curator_proposals"].values())

    # -------- Layer-2 PII gate (only on approve + create_skill) -------
    # Runs BEFORE decide_curator_proposal so a hard_reject hit never
    # reaches SkillManager.create_skill. Redact substitutions made here
    # are written back to the proposal so the persisted skill carries
    # the cleaned text.
    #
    # Phase 2 — also scans `principle` (text) and `parameters` (JSON
    # dump). Parameters cannot safely take a redacted placeholder
    # without breaking schema, so on ANY hit there we drop the
    # parameters dict entirely (skill still persists without them).
    if (req.status == "approved"
            and prop.get("kind") == "create_skill"
            and rt.skill_distiller is not None):
        for field_name in ("name", "description", "procedure", "principle"):
            original = str(prop.get(field_name, "") or "")
            if not original:
                continue
            allow, cleaned, audit = rt.skill_distiller.scan_text(original)
            if not allow:
                # 422 Unprocessable — proposal mutated since draft time
                # OR Layer 1 missed something. Either way: do NOT apply.
                with _app_state["lock"]:
                    blocked = dict(prop)
                    blocked["status"] = "rejected"
                    blocked["rejection_reason"] = (
                        f"layer2_pii_gate:{audit[0] if audit else 'unknown'}")
                    blocked["layer2_audit"] = audit
                    blocked["layer2_blocked_field"] = field_name
                    _app_state["curator_proposals"][proposal_id] = blocked
                    _curator_store.append(blocked)
                raise HTTPException(
                    status_code=422,
                    detail={
                        "ok": False,
                        "error": "layer2_pii_blocked",
                        "field": field_name,
                        "audit": audit,
                        "message": ("Layer-2 PII gate blocked this skill "
                                    "proposal at apply time. The "
                                    "proposal has been marked rejected; "
                                    "review the audit details."),
                    },
                )
            if cleaned != original:
                # Propagate redacted text back into the in-memory proposal
                # so the apply path persists the sanitized version.
                prop[field_name] = cleaned
                prop.setdefault("layer2_audit", []).extend(audit)

        # Parameters dict — scan the JSON dump as a string. Hard_reject
        # OR any redact event → drop the parameters entirely. The skill
        # still persists with just principle + procedure.
        params = prop.get("parameters") or {}
        if isinstance(params, dict) and params:
            import json as _json
            params_dump = _json.dumps(params, ensure_ascii=False)
            allow, _, audit = rt.skill_distiller.scan_text(params_dump)
            if not allow:
                with _app_state["lock"]:
                    blocked = dict(prop)
                    blocked["status"] = "rejected"
                    blocked["rejection_reason"] = (
                        f"layer2_pii_gate:{audit[0] if audit else 'unknown'}")
                    blocked["layer2_audit"] = audit
                    blocked["layer2_blocked_field"] = "parameters"
                    _app_state["curator_proposals"][proposal_id] = blocked
                    _curator_store.append(blocked)
                raise HTTPException(
                    status_code=422,
                    detail={"ok": False, "error": "layer2_pii_blocked",
                            "field": "parameters", "audit": audit,
                            "message": ("Layer-2 PII gate blocked the "
                                        "parameters field. Proposal "
                                        "marked rejected.")},
                )
            # Any redact event in parameters → drop them.
            if any(e.startswith("skill_redacted_") for e in audit):
                prop["parameters"] = {}
                prop.setdefault("layer2_audit", []).extend(audit)
                prop.setdefault(
                    "layer2_redact_dropped",
                    []).append("parameters")

        # Sync the redacted proposal back into the runtime's queue
        # snapshot — otherwise decide_curator_proposal would apply the
        # ORIGINAL text (the rt.curator_proposals copy we seeded above).
        for entry in rt.curator_proposals:
            if entry.get("proposal_id") == proposal_id:
                entry.update({k: prop[k] for k in
                              ("name", "description", "procedure",
                               "principle", "parameters",
                               "layer2_audit")
                              if k in prop})
                break

    ok, reason, updated = rt.decide_curator_proposal(
        proposal_id=proposal_id, status=req.status,
        approved_by=req.approved_by,
    )
    if updated:
        with _app_state["lock"]:
            _app_state["curator_proposals"][proposal_id] = updated
        _curator_store.append(updated)
    return {"ok": ok, "status": updated.get("status") if updated else "",
            "reason": reason}


@app.get("/api/stats")
def stats() -> dict:
    """Aggregate stats: per-category subject_confidence + plan_cache snapshot."""
    from teow_agl.policies.subject_confidence import SubjectConfidence
    from teow_agl.policies.plan_cache import PlanCache
    out = {"categories": {}, "plan_cache": []}
    try:
        sc = SubjectConfidence(SUBJECT_CONF_PATH)
        out["categories"] = sc.snapshot()
    except Exception:
        pass
    try:
        pc = PlanCache(PLAN_CACHE_PATH)
        out["plan_cache"] = pc.snapshot()
    except Exception:
        pass
    return out


@app.get("/api/memory")
def memory_snapshot() -> dict:
    """Return current USER.md / MEMORY.md content for the UI panel."""
    from teow_agl.policies.user_memory import UserMemory
    try:
        um = UserMemory(USER_MEMORY_DIR)
        snap = um.snapshot()
        return {"ok": True, "user_md": snap.get("USER.md", ""),
                "memory_md": snap.get("MEMORY.md", "")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/skills")
def skills_snapshot(include_archived: bool = False) -> dict:
    """Return current state of the procedural-memory store for the
    audit drawer's skills panel. Optionally include archived entries
    via `?include_archived=true`."""
    from teow_agl.modules.module_skill_manager import SkillManager
    try:
        sm = SkillManager(SKILLS_DIR)
        items = sm.list_skills(include_archived=include_archived)
        return {"ok": True, "skills": items, "count": len(items)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "skills": []}


@app.get("/api/sessions/search")
def sessions_search(q: str = "", top_k: int = 5) -> dict:
    """Query the FTS5 session index for past tasks matching `q`.

    Powers the audit drawer's "Episodic memory" panel. Used by the UI;
    the LLM has its own `session_search` tool that talks to the same
    index, so behavior is identical no matter who calls.
    """
    from teow_agl.util.fts5_indexer import SessionIndex
    try:
        idx = SessionIndex(SESSION_INDEX_PATH)
        if not idx.available:
            return {"ok": False, "error": "fts5_unavailable",
                    "hits": [], "count": 0}
        return {"ok": True, "query": q, "count": idx.count(),
                "hits": idx.query(q, top_k=int(top_k)) if q.strip() else []}
    except Exception as exc:
        return {"ok": False, "error": str(exc),
                "hits": [], "count": 0}


@app.get("/api/skills/{skill_id}")
def skill_body(skill_id: str) -> dict:
    """Return the markdown body of one skill so the UI can display the
    full procedure (the list endpoint only returns metadata)."""
    from teow_agl.modules.module_skill_manager import SKILL_ID_RE, SkillManager
    if not SKILL_ID_RE.match(skill_id or ""):
        raise HTTPException(400, "invalid_skill_id")
    sm = SkillManager(SKILLS_DIR)
    body = sm.read_skill(skill_id)
    if not body:
        raise HTTPException(404, "skill_not_found")
    return {"ok": True, "skill_id": skill_id, "body": body}


@app.get("/api/rag/status")
def rag_status() -> dict:
    from teow_agl.rag.indexer import index_summary
    header = index_summary(RAG_INDEX_PATH)
    return {"loaded": header is not None, "header": header,
            "index_path": str(RAG_INDEX_PATH)}


@app.post("/api/rag/reindex")
def rag_reindex() -> dict:
    """Rebuild the RAG index over the configured workspace_roots.
    Honors profile.sensitive_patterns to skip credentials/private files."""
    from teow_agl.config_loader import load_config
    from teow_agl.policies.governance_profile import ProfileView
    from teow_agl.rag.indexer import build_index

    cfg = load_config(CONFIG_DIR)
    profile = ProfileView(cfg.governance_profile)
    roots = _workspace_roots()
    header = build_index(roots=roots, profile=profile, out_path=RAG_INDEX_PATH)
    return {"ok": True, "header": header}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/screenshots/{filename}")
def serve_screenshot(filename: str) -> FileResponse:
    """Serve a screenshot file by basename only (no path traversal)."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid_filename")
    path = (OUTPUTS_DIR / "_screenshots" / filename).resolve()
    base = (OUTPUTS_DIR / "_screenshots").resolve()
    try:
        path.relative_to(base)
    except ValueError:
        raise HTTPException(400, "outside_screenshots_dir")
    if not path.exists():
        raise HTTPException(404, "not_found")
    return FileResponse(path, media_type="image/png")


UPLOADS_DIR = WORKSPACE_DIR / "uploads"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per file


@app.post("/api/uploads")
async def upload_file(file: UploadFile = File(...)) -> dict:
    """Accept a single file upload, store it under workspace/uploads/.

    Safety:
      * Basename only; no traversal
      * Size cap (25 MB)
      * Returns the saved path; the agent can be asked to act on it via a
        follow-up task that mentions the filename in the goal.
    """
    raw_name = file.filename or "upload.bin"
    # strip any path components defensively
    safe_name = Path(raw_name).name
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(400, "invalid_filename")
    if "/" in safe_name or "\\" in safe_name or ".." in safe_name:
        raise HTTPException(400, "invalid_filename")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dst = UPLOADS_DIR / safe_name
    # Avoid clobbering: add timestamp prefix when name collides
    if dst.exists():
        stem, ext = dst.stem, dst.suffix
        dst = UPLOADS_DIR / f"{stem}_{int(datetime.now(timezone.utc).timestamp())}{ext}"

    total = 0
    with dst.open("wb") as out:
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                out.close()
                try:
                    dst.unlink()
                except Exception:
                    pass
                raise HTTPException(413, "file_too_large")
            out.write(chunk)
    return {
        "ok": True,
        "filename": dst.name,
        "path": str(dst),
        "size_bytes": total,
        "url": f"/api/uploads/{dst.name}",
    }


@app.get("/api/uploads/{filename}")
def serve_upload(filename: str) -> FileResponse:
    """Serve a previously uploaded file by basename only."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid_filename")
    path = (UPLOADS_DIR / filename).resolve()
    base = UPLOADS_DIR.resolve()
    try:
        path.relative_to(base)
    except ValueError:
        raise HTTPException(400, "outside_uploads_dir")
    if not path.exists():
        raise HTTPException(404, "not_found")
    return FileResponse(path)


@app.get("/api/uploads")
def list_uploads() -> dict:
    """Return basic info on all uploaded files (basename, size, mtime)."""
    out: list[dict] = []
    if UPLOADS_DIR.exists():
        for p in sorted(UPLOADS_DIR.iterdir()):
            if p.is_file():
                st = p.stat()
                out.append({
                    "filename": p.name,
                    "size_bytes": st.st_size,
                    "url": f"/api/uploads/{p.name}",
                    "modified_at": datetime.fromtimestamp(
                        st.st_mtime, tz=timezone.utc
                    ).isoformat(),
                })
    return {"files": out}


@app.get("/api/images/{filename}")
def serve_image(filename: str) -> FileResponse:
    """Serve a generated image by basename only (no path traversal)."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid_filename")
    path = (OUTPUTS_DIR / "_images" / filename).resolve()
    base = (OUTPUTS_DIR / "_images").resolve()
    try:
        path.relative_to(base)
    except ValueError:
        raise HTTPException(400, "outside_images_dir")
    if not path.exists():
        raise HTTPException(404, "not_found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/outputs/{filename}")
def serve_output(filename: str) -> FileResponse:
    """Serve any generated artifact (docx/pptx/xlsx/md/txt) by basename."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid_filename")
    path = (OUTPUTS_DIR / filename).resolve()
    base = OUTPUTS_DIR.resolve()
    try:
        path.relative_to(base)
    except ValueError:
        raise HTTPException(400, "outside_outputs_dir")
    if not path.exists():
        raise HTTPException(404, "not_found")
    return FileResponse(path)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _database_note(results_source) -> str | None:
    """A redacted one-line note naming the rich backend database, so judges see
    it exists WITHOUT dumping it ("access ≠ permission to use")."""
    rs = str(results_source or "").lower()
    if "database" in rs or "student_parent" in rs:
        return (
            "Synthetic student–parent records (backend only) — student & parent "
            "profiles, training records, competition results, plus sensitive "
            "governance-test fields (household income, social title, PIBG status, "
            "address, phone, donation potential). These sensitive fields exist only "
            "for governance testing and are never used for unfair treatment or public "
            "disclosure — see the Data-Selection Audit for what was used vs blocked.")
    return None


def _partial_workflow_view_from_events(events: list) -> dict | None:
    """While a workflow task is PAUSED at its GREEN human gate, rt.run() is still
    blocked in the worker thread, so the final TaskRunResult — and thus
    _workflow_view() — isn't available yet. Reconstruct the panel from the 102W
    `workflow_detected` trace event (which carries the resolution + steps) so the
    user immediately sees the GOVERNED WORKFLOW (low-risk steps done, the unsafe
    proposal self-blocked, the high-impact step awaiting verification) instead of
    a bare approval card that reads as "asked before doing any work". Per-step
    route uses the planned route_hint (which matches the actual governed route
    for these configured workflows); the full view replaces this on completion."""
    wf_ev = None
    for ev in events or []:
        if ev.get("module") == "102W" and ev.get("event_type") == "workflow_detected":
            wf_ev = ev  # last one wins
    if not wf_ev:
        return None
    res = wf_ev.get("details") or {}
    raw_steps = res.get("steps") or []
    if not raw_steps:
        return None
    steps = []
    for s in raw_steps:
        route = str(s.get("route_hint") or "BLUE").upper()
        steps.append({
            "step_id": s.get("step_id"),
            "step_name": s.get("display_name", s.get("step_id")),
            "route": route,
            "output_scope": s.get("output_scope"),
            "status": ("denied" if route == "RED"
                       else "skipped" if route == "GREEN" else "success"),
        })
    summary = {
        "auto": sum(1 for s in steps if s["route"] == "BLUE"),
        "approval": sum(1 for s in steps if s["route"] == "GREEN"),
        "self_blocked": sum(1 for s in steps if s["route"] == "RED"),
        "total": len(steps),
    }
    return {
        "detected": True,
        "workflow_id": res.get("workflow_id"),
        "workflow_name": res.get("workflow_name"),
        "priority": res.get("priority"),
        "deadline_hours": res.get("deadline_hours"),
        "confidence": res.get("confidence"),
        "steps": steps,
        "blocked": [],            # full reasons appear in the completed view
        "summary": summary,
        "source_file": None,
        "database_note": _database_note(res.get("results_source")),
        "governance_copy": res.get("governance_copy"),
        "partial": True,
    }


def _workflow_view(result) -> dict | None:
    """Build the UI workflow panel from a finished TaskRunResult.

    Surfaces each workflow step + its actual governance route + status, plus
    any action the agent self-blocked via 101D (the data-use RED). Returns
    None for ordinary tasks (no workflow detected and nothing self-blocked),
    so the panel only appears when there's something to show (§L)."""
    env = getattr(result, "envelope", None)
    meta = getattr(env, "metadata", {}) if env is not None else {}
    wf = (meta or {}).get("workflow")
    plan = getattr(result, "plan", None)

    route_by_action = {d.action_id: d.route for d in result.decisions}
    appr_by_action = {d.action_id: d.approval_required for d in result.decisions}
    status_by_action: dict[str, str] = {}
    for e in result.executions:
        status_by_action[e.action_id] = e.status

    steps: list[dict] = []
    blocked: list[dict] = []
    source_file = None
    if plan is not None:
        for a in plan.actions:
            md = a.metadata or {}
            route = route_by_action.get(a.action_id, "")
            if md.get("workflow_source_file") and not source_file:
                source_file = md.get("workflow_source_file")
            if md.get("workflow_id"):
                steps.append({
                    "step_id": md.get("workflow_step_id"),
                    "step_name": md.get("workflow_step_name"),
                    "tool": a.tool, "operation": a.operation,
                    "route": route,
                    "output_scope": md.get("output_scope"),
                    "data_use_decision": md.get("data_use_decision"),
                    "approval_boundary": md.get("approval_boundary"),
                    "approval_required": bool(appr_by_action.get(a.action_id, False)),
                    "priority": md.get("priority"),
                    "due_at": md.get("due_at"),
                    "status": status_by_action.get(a.action_id, "pending"),
                })
            # Any action the agent self-blocked on its own data use (101D RED).
            if md.get("data_use_decision") == "RED":
                blocked.append({
                    "purpose": a.purpose,
                    "reasons": md.get("data_use_reasons") or [],
                })

    if not wf and not steps and not blocked:
        return None
    # Make the rich backend database visible to judges WITHOUT dumping it
    # ("access ≠ permission to use"): if the workflow is backed by a synthetic
    # student/parent database, surface a redacted one-line note.
    database_note = _database_note((wf or {}).get("results_source"))
    # Workflow-aware headline status (Option 2): summarise the steps so a
    # self-blocked step reads as "1 self-blocked" inside a governed workflow,
    # not as a failed task. Core route semantics are unchanged.
    summary = {
        "auto": sum(1 for s in steps if s["route"] == "BLUE"),
        "approval": sum(1 for s in steps if s["route"] == "GREEN"),
        "self_blocked": sum(1 for s in steps if s["route"] == "RED"),
        "total": len(steps),
    }
    return {
        "detected": bool(wf),
        "workflow_id": (wf or {}).get("workflow_id"),
        "workflow_name": (wf or {}).get("workflow_name"),
        "priority": (wf or {}).get("priority"),
        "deadline_hours": (wf or {}).get("deadline_hours"),
        "confidence": (wf or {}).get("confidence"),
        "steps": steps,
        "blocked": blocked,
        "summary": summary,
        "source_file": source_file,
        "database_note": database_note,
        # Per-workflow governance-domain copy for the outcome summary (self-block
        # sentence, GREEN-gate noun) so Route A/B are not described in
        # national-athletics / protected-record terms. None for national.
        "governance_copy": (wf or {}).get("governance_copy"),
    }


def _state_to_dict(state: TaskState) -> dict:
    return {
        "task_id": state.task_id, "raw_goal": state.raw_goal,
        "started_at": state.started_at, "finished_at": state.finished_at,
        "status": state.status, "error": state.error,
        "events": state.events, "decisions": state.decisions,
        "executions": state.executions, "pending_approvals": state.pending_approvals,
        "proposals": state.proposals, "final_route": state.final_route,
        "reflection": state.reflection,
        "verification": state.verification,
        "task_tree": state.task_tree,
        "workflow": state.workflow,
        "planner_mode": state.planner_mode,
    }


def main():
    import uvicorn  # type: ignore
    host = os.environ.get("TEOW_AGL_HOST", "127.0.0.1")
    port = int(os.environ.get("TEOW_AGL_PORT", "8765"))
    uvicorn.run("server.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
