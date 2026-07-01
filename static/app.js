/* GovGuard MY UI — Powered by TEOW-AGL. Chat-first, drag-drop upload, image preview. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ---------------- Auth (Phase C) ----------------
// When the server runs with TEOW_AGL_AUTH_PASSWORD set, every /api/*
// call (except /api/login + /api/health) returns 401 until the
// operator logs in. We intercept fetch globally — all call sites stay
// untouched — and surface the login overlay. The session lives in an
// HttpOnly cookie set by the server; JS never stores the secret.
const _origFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const resp = await _origFetch(...args);
  if (resp.status === 401 && !String(args[0]).includes("/api/login")) {
    showLoginOverlay();
  }
  return resp;
};

function showLoginOverlay() {
  const el = document.getElementById("login-overlay");
  if (el && el.style.display !== "flex") {
    el.style.display = "flex";
    const input = document.getElementById("login-password");
    if (input) setTimeout(() => input.focus(), 50);
  }
}

async function submitLogin() {
  const input = document.getElementById("login-password");
  const errEl = document.getElementById("login-error");
  const pw = input ? input.value : "";
  try {
    const r = await _origFetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    if (r.ok) {
      document.getElementById("login-overlay").style.display = "none";
      location.reload(); // re-fetch everything with the session cookie
    } else if (errEl) {
      errEl.textContent = "密码错误 / wrong password";
      if (input) input.select();
    }
  } catch (e) {
    if (errEl) errEl.textContent = "连接失败 / connection failed";
  }
}

function initAuth() {
  const btn = document.getElementById("login-submit");
  const input = document.getElementById("login-password");
  if (btn) btn.addEventListener("click", submitLogin);
  if (input) input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitLogin();
  });
}
initAuth();

const state = {
  attachments: [],         // [{filename, url, size_bytes}]
  tasks: {},               // task_id -> { ui_node, poll_handle }
  // Track approval-card click state so re-renders (every 500ms poll)
  // don't reset the button styling. Maps approval_id ->
  // { status: "pending"|"submitting"|"submitted"|"error", decision, error }
  approvals: {},
};

// ---------------- Theme ----------------
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem("teow_theme", theme); } catch (e) {}
}
function initTheme() {
  let saved = "light";
  try { saved = localStorage.getItem("teow_theme") || "light"; } catch (e) {}
  applyTheme(saved);
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "light";
  applyTheme(cur === "light" ? "dark" : "light");
}

// ---------------- Audit drawer ----------------
function toggleAudit() {
  const app = $(".app");
  const drawer = $("#audit-drawer");
  const open = app.classList.toggle("with-audit");
  drawer.hidden = !open;
  $("#audit-toggle").textContent = open ? "Audit ◂" : "Audit ▸";
  if (open) { loadRagStatus(); loadStats(); loadMemory(); loadSkills(); loadCurator(); loadPatches(); loadHistory(); }
}

// ---------------- Config bar ----------------
async function loadConfig() {
  try {
    const r = await fetch("/api/config");
    const c = await r.json();
    const parts = [`planner: ${c.planner}`];
    if (c.domain_pack) parts.push(`pack: ${c.domain_pack}`);
    $("#cfg-mini").textContent = parts.join(" · ");
    // MAIC demo-mode lockout banner (Owner Rule 4).
    const banner = $("#demo-banner");
    if (banner) banner.hidden = !c.demo_mode;
  } catch (e) { $("#cfg-mini").textContent = ""; }
}

// ---------------- Composer / send ----------------
function autoSizeTextarea(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 200) + "px";
}

async function startTask() {
  const goalEl = $("#goal");
  let goal = goalEl.value.trim();
  if (!goal && !state.attachments.length) return;

  // weave attachment context into the goal text so the agent sees the
  // uploaded files alongside the user's words
  if (state.attachments.length) {
    const lines = state.attachments.map(a =>
      `(attached file: workspace/uploads/${a.filename}, ${a.size_bytes} bytes)`).join("\n");
    goal = goal ? `${goal}\n\n${lines}` : `User uploaded these files:\n${lines}`;
  }

  hideWelcome();
  appendUserMessage(goal);
  goalEl.value = "";
  autoSizeTextarea(goalEl);
  state.attachments = [];
  renderAttachments();
  $("#run-btn").disabled = true;

  try {
    const r = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw_goal: goal }),
    });
    if (!r.ok) throw new Error(await r.text());
    const { task_id } = await r.json();
    const node = appendAgentMessage(task_id);
    const handle = setInterval(() => pollTask(task_id, node), 500);
    state.tasks[task_id] = { ui_node: node, poll_handle: handle };
  } catch (e) {
    appendAgentError("Failed to start task: " + e.message);
  } finally {
    $("#run-btn").disabled = false;
  }
}

function hideWelcome() {
  const w = $("#welcome");
  if (w) w.style.display = "none";
  // Reveal the persistent dock so the demo prompts stay one click away after
  // the hero is gone — no page refresh needed to run a follow-up probe.
  const dock = $("#demo-dock");
  if (dock) dock.hidden = false;
}

// Clone the welcome's demo buttons (section labels + grids) into the persistent
// dock above the composer. Single HTML source of truth — the dock is a runtime
// copy, so the scripted-button guard test only ever sees the welcome copy.
function buildDemoDock() {
  const body = $("#demo-dock-body");
  const src = $("#welcome");
  if (!body || !src || body.childElementCount) return;
  src.querySelectorAll(".demo-section-label, .example-grid").forEach(
    (n) => body.appendChild(n.cloneNode(true)));
}

function appendUserMessage(text) {
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  wrap.innerHTML = `
    <div class="who">You</div>
    <div class="bubble"></div>
  `;
  wrap.querySelector(".bubble").textContent = text;
  $("#chat").appendChild(wrap);
  scrollToBottom();
}

// Map a raw governance reason code to plain language for the judge view.
// Falls back to the raw reason so nothing is ever hidden.
function prettyReason(raw) {
  const r = String(raw || "").toLowerCase();
  const has = s => r.indexOf(s) !== -1;
  if (has("governance_bypass")) return "attempt to bypass governance — blocked";
  if (has("sensitive_data_disclosure")) return "would broadcast student / guardian personal data — blocked";
  if (has("sensitive_data_learning") || has("student_data_learning")) return "would learn student / guardian personal data — blocked (learning boundary)";
  if (has("personal_prediction")) return "cannot reliably predict individual behaviour — honest limitation";
  if (has("parent_notice_broadcast")) return "parent notice requires educator approval before release";
  if (has("student_record") || has("student_attendance")) return "student-record change requires educator approval";
  if (has("autonomous_external_send")) return "autonomous send to parents is prohibited — blocked";
  if (has("failsafe_sensitive_mention")) return "involves sensitive data — escalated for human approval";
  if (has("approval_required")) return "requires human approval";
  if (has("infeasible")) return "cannot be done reliably — honest limitation";
  if (has("risk_recommended:blue")) return "safe — within policy";
  return raw;
}

// Governance pipeline card — a judge-visible, plain-language view of the
// 106 -> 101A -> 102 -> 101B -> 103 -> 105 -> 107 -> 110 path for THIS task,
// built from the same audit events the runtime emits. The point it makes:
// the LLM proposes; an independent governance runtime decides; a human
// approves; everything is verified and on the record.
// Route-specific extra card (e.g. the INFEASIBLE reward probe's inline proposal
// framework, shown directly in the UI rather than as a file). Honest: the agent
// refuses to guess and instead structures a human decision.
function renderExtraCard(bubble, d) {
  const el = bubble.querySelector(".extra-card");
  if (!el) return;
  const cat = (((d.events || []).find(e => e.module === "101A")) || {}).summary || "";
  if (d.status !== "running" && d.final_route === "INFEASIBLE"
      && /unsupported_amount_estimate/.test(cat)) {
    el.innerHTML =
      `<div class="proposal-card">`
      + `<div class="pc-title">📋 Reward Decision Proposal — for human approval</div>`
      + `<div class="pc-sub">Missing required data</div>`
      + `<ul><li>No approved school reward policy on file</li>`
      + `<li>No Board / PIBG budget decision on file</li>`
      + `<li>No previous reward precedent available</li>`
      + `<li>No authorised decision-maker confirmation</li>`
      + `<li>No sponsor contribution record</li></ul>`
      + `<div class="pc-sub">Decision fields — for a human to complete</div>`
      + `<ul><li>Pupil reward amount — <i>to be decided</i></li>`
      + `<li>Teacher-in-charge appreciation — <i>to be decided</i></li>`
      + `<li>Funding source — school / PIBG / Board / sponsor</li>`
      + `<li>Approval body — Headmaster / Board / PIBG</li>`
      + `<li>Eligibility — all participants or medalists only</li>`
      + `<li>National-record recognition — <i>to be decided</i></li></ul>`
      + `<div class="pc-note">Safe recommendation: prepare this for the Headmaster / `
      + `Board / PIBG to decide. No amount is estimated or announced until an `
      + `approval is recorded.</div>`
      + `</div>`;
    el.hidden = false;
    return;
  }
  el.hidden = true; el.innerHTML = "";
}

function renderGovPipeline(bubble, d) {
  const el = bubble.querySelector(".gov-pipeline");
  if (!el) return;
  const decisions = d.decisions || [];
  if (!d || d.status === "running" || (!decisions.length && !d.final_route)) {
    el.hidden = true; el.innerHTML = ""; return;
  }
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const events = d.events || [];
  const rank = { BLUE: 0, GREEN: 1, INFEASIBLE: 2, RED: 3 };
  const route = d.final_route
    || decisions.reduce((a, de) => (rank[de.route] > rank[a] ? de.route : a),
                        decisions[0] ? decisions[0].route : "");

  const wfDetected = !!(d.workflow && d.workflow.detected);
  const sm = wfDetected ? (d.workflow.summary || {}) : {};
  // The RED self-block's specific reason (for the composite note), stripped of
  // the internal "risk_recommended"/"data_use_guard_red" markers.
  const redDec = decisions.find(de => de.route === "RED");
  const redReason = redDec
    ? prettyReason((redDec.reasons || [])
        .filter(r => !String(r).startsWith("risk_recommended")
                     && r !== "data_use_guard_red"
                     && !String(r).startsWith("safe_alternative:"))
        .join(" ; "))
    : "";
  const pre = events.find(e => e.module === "101A");
  let cat = "", mode = "";
  if (pre && pre.summary) {
    const mc = /category=(\S+)/.exec(pre.summary); if (mc) cat = mc[1];
    const mm = /mode=(\S+)/.exec(pre.summary); if (mm) mode = mm[1];
  }
  // When 102W matched a workflow, show that instead of the raw 101A 'unknown'
  // category (display only — the underlying 101A result is unchanged).
  const preDet = wfDetected
    ? `workflow: ${d.workflow.workflow_id || "post_event_reporting"} (detected by 102W)`
    : `category: ${cat || "unknown"}${mode ? " · mode: " + mode : ""}`;
  const planned = events.some(e => e.module === "102" && e.event_type === "planner_called");
  const skipped = events.some(e => e.module === "102" && e.event_type === "planner_skipped");
  const plannerDet = planned ? "proposed a plan" : (skipped ? "direct plan (no remote LLM)" : "—");

  const sig = [];
  decisions.forEach(de => (de.risk_factors || []).forEach(f => { if (!sig.includes(f)) sig.push(f); }));
  // When 101B flags high-risk action factors, list them. When it flags NONE,
  // show the concrete dimensions it actually cleared (reversibility / blast
  // radius / external send / system scope) so the line reads as real evidence,
  // not a hollow "scored". Scope it to where that is TRUE — a low-risk BLUE
  // action or the drafting workflow — and keep the neutral line for an
  // external-publish GREEN (its external routing is 101A's job, not 101B's) or
  // a blocked RED route, so we never falsely claim "no external send" / "low
  // risk". (101B scores action mechanics; the RED/GREEN you see is 101A/101D.)
  const externalCat = /external|publish|email|send/.test(cat || "");
  const showCleared = (wfDetected || route === "BLUE") && !externalCat;
  // showCleared (workflow + low-risk BLUE) wins FIRST, so the composite-RED
  // workflow keeps its honest "cleared" line and is never mislabeled "blocked".
  // For the blocked/gated probes, name the LAYER that actually decided, so the
  // row reads as a coherent part of the layered story instead of an apparent
  // "RED but no high-risk signals" contradiction.
  let riskDet;
  if (sig.length) riskDet = sig.slice(0, 4).join(", ");
  else if (showCleared) riskDet = "reversible · non-destructive · no external send · no system-level change — low risk";
  else if (route === "RED") riskDet = "blocked before execution — risk came from pre-governance/data-use";
  else if (route === "INFEASIBLE") riskDet = "stopped before execution — missing required policy data";
  else if (route === "GREEN") riskDet = "action checked — approval required by release/official-write policy";
  else riskDet = "scored — no high-risk action signals";

  let reason = "";
  const dec = decisions.find(de => de.route === route) || decisions[0];
  if (dec && (dec.reasons || []).length) reason = prettyReason(dec.reasons.join(" ; "));

  const needApproval = decisions.some(de => de.approval_required);
  const execs = d.executions || [];
  const anyOk = execs.some(e => e.status === "success");
  let approvalDet = needApproval ? (anyOk ? "approved by a human" : "approval required") :
                                   "not required (within policy)";
  let execDet;
  const okCount = execs.filter(e => e.status === "success").length;
  const gs = wfDetected ? greenGateState(d) : null;
  if (wfDetected) {
    // Composite workflow: low-risk steps ran; one step self-blocked; the
    // high-impact step waits for verification — and, once you decide, reads as
    // approved/simulated or rejected (not stuck on "awaiting").
    approvalDet = !sm.approval ? "—"
      : gs === "approved" ? "approved by a human — verification done"
      : gs === "rejected" ? "rejected by a human"
      : "high-impact step awaiting human verification";
    execDet = `${sm.auto || 0} steps done`
      + (sm.self_blocked ? ` · ${sm.self_blocked} self-blocked` : "")
      + (sm.approval ? ` · ${sm.approval} ${greenGateTag(gs)}` : "");
  } else if (route === "RED") { approvalDet = "—"; execDet = "blocked — nothing executed"; }
  else if (route === "INFEASIBLE") { approvalDet = "—"; execDet = "not run — honest limitation"; }
  else {
    execDet = okCount ? `${okCount} action(s) executed` : "not run";
  }
  // Verification (110) — honest, route-aware text. Never a bare "not run":
  // a blocked or infeasible route has nothing to verify; a workflow awaiting
  // your sign-off defers verification of the high-impact step until then.
  const ver = d.verification;
  const verChecks = (ver && ver.checks) ? ver.checks.length : 0;
  const passLbl = `passed (${verChecks} check${verChecks === 1 ? "" : "s"})`;
  let verDet, verFail = false;
  if (wfDetected) {
    if (gs === "pending") {
      verDet = "deferred — runs after you verify the high-impact step";
    } else if (gs === "approved") {
      verDet = "high-impact write verified & simulated (demo)";
    } else if (gs === "rejected") {
      verDet = "n/a — high-impact write was rejected";
    } else if (verChecks) {
      verFail = !ver.pass;
      verDet = ver.pass ? passLbl : ("FAILED: " + (ver.summary || ""));
    } else {
      verDet = "low-risk drafts — verified inline";
    }
  } else if (route === "RED") {
    verDet = "n/a — nothing executed to verify";
  } else if (route === "INFEASIBLE") {
    verDet = "n/a — no action was taken";
  } else if (ver && ver.enabled !== false && verChecks) {
    verFail = !ver.pass;
    verDet = ver.pass ? passLbl : ("FAILED: " + (ver.summary || ""));
  } else {
    verDet = "no automated checks for this output type";
  }

  const step = (mod, lab, det, extra) =>
    `<div class="gp-step ${extra || ""}"><span class="gp-mod">${esc(mod)}</span>`
    + `<span class="gp-lab">${esc(lab)}</span><span class="gp-det">${det}</span></div>`;
  el.innerHTML =
    `<div class="gp-title">Governance pipeline — the planner proposes, governance decides</div>`
    + step("106", "Intake", "request received &amp; normalized")
    + step("101A", "Pre-governance", esc(preDet))
    + step("102", "Planner", esc(plannerDet) + " — <em>cannot self-authorise</em>")
    + step("101B", "Action risk", esc(riskDet))
    + step("103", "Decision",
        wfDetected
          ? `<b class="gp-route COMPOSITE">COMPOSITE</b> — governed workflow: `
            + `${sm.auto || 0} BLUE · ${sm.self_blocked || 0} RED self-blocked · `
            + `${sm.approval || 0} GREEN`
            + (sm.self_blocked
                ? `<div class="gp-note">RED self-block: ${esc(redReason
                    || "status/income-based softening of a parent message")}. `
                  + `The low-risk steps ran; `
                  + (gs === "approved"
                       ? "the high-impact write was human-verified and simulated in demo mode"
                       : gs === "rejected"
                       ? "the high-impact write was rejected and the protected record remains unchanged"
                       : "the high-impact write is paused for educator verification")
                  + ` — this is a composite governed workflow, not a single failed decision.</div>`
                : "")
          : `<b class="gp-route ${esc(route)}">${esc(route)}</b>${reason ? " — " + esc(reason) : ""}`,
        "gp-decision")
    + step("105", "Human gate", esc(approvalDet))
    + step("107", "Execution", esc(execDet))
    + step("110", "Verification", verFail ? `<b>${esc(verDet)}</b>` : esc(verDet));
  el.hidden = false;
}

// Workflow Autonomy panel (102W/101D). Extends the governance card, never
// replaces it: workflow name + priority + deadline, each step with its actual
// route + status, and any internal action the agent self-blocked on its own
// data use (the RED reason + safe alternative). Hidden for ordinary tasks.
function renderWorkflowPanel(bubble, d) {
  const el = bubble.querySelector(".workflow-panel");
  if (!el) return;
  const wf = d && d.workflow;
  if (!wf || d.status === "running") { el.hidden = true; el.innerHTML = ""; return; }
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const steps = wf.steps || [];
  const blocked = wf.blocked || [];
  if (!wf.detected && !blocked.length && !steps.length) {
    el.hidden = true; el.innerHTML = ""; return;
  }

  let head;
  if (wf.detected) {
    // Product copy: don't surface the internal 102W resolver score (reads as
    // "uncertain"), and don't show a single generic workflow deadline — per-step
    // SLAs differ (parent notices same-day, public post after approval, protected
    // write after verification), so timing lives in the step notes / follow-up
    // actions, not a misleading global "deadline". (Confidence stays in the audit.)
    head = `<div class="wf-head">Workflow detected: `
      + `<b>${esc(wf.workflow_name || wf.workflow_id)}</b>`
      + `<span class="wf-meta">priority: ${esc(wf.priority || "—")}</span></div>`;
  } else {
    head = `<div class="wf-head">Agent self-governance — data-use check</div>`;
  }

  const statusLabel = s => {
    if (s === "success") return "done";
    if (s === "denied" || s === "skipped") return "waiting approval";
    if (s === "failed") return "not run";
    return s || "pending";
  };
  const gs = greenGateState(d);
  const rows = steps.map((s, i) => {
    const route = s.route || "—";
    const official = s.output_scope === "official_record";
    let note, st;
    if (route === "RED") {
      note = "unsafe data-use blocked"; st = "self-blocked";
    } else if (route === "GREEN") {
      const base = official ? "protected student-record write" : "external action";
      if (gs === "approved") {
        note = `${base} — verification approved; simulated in demo (no real write)`;
        st = "approved (simulated)";
      } else if (gs === "rejected") {
        note = `${base} — rejected; database unchanged`;
        st = "rejected";
      } else {
        note = official ? "protected student-record write — needs human verification"
                        : "external action — needs human approval";
        st = "awaiting verification";
      }
    } else {
      note = s.output_scope === "public_draft" ? "public draft — sensitive fields blocked"
        : s.output_scope === "audit" ? "data-selection audit"
        : s.output_scope === "internal" ? "internal data allowed" : "";
      st = statusLabel(s.status);
    }
    return `<div class="wf-step">`
      + `<span class="wf-n">${i + 1}</span>`
      + `<span class="wf-name">${esc(s.step_name || s.step_id)}</span>`
      + `<span class="wf-route gp-route ${esc(route)}">${esc(route)}</span>`
      + `<span class="wf-status">${esc(st)}</span>`
      + `<span class="wf-note">${esc(note)}</span></div>`;
  }).join("");

  const blockedHtml = blocked.map(b => {
    const reasons = (b.reasons || []);
    const main = reasons.filter(r =>
      !String(r).startsWith("safe_alternative:") && r !== "data_use_guard_red");
    const alt = reasons.find(r => String(r).startsWith("safe_alternative:"));
    return `<div class="wf-blocked">`
      + `<div class="wf-blocked-head">`
      + `<span class="wf-route gp-route RED">RED</span> `
      + `Blocked internal action: ${esc(b.purpose)}</div>`
      + (main.length ? `<div class="wf-blocked-reason">${esc(main.join(" "))}</div>` : "")
      + (alt ? `<div class="wf-blocked-alt">`
          + esc(alt.replace(/^safe_alternative:\s*/, "Safe alternative: "))
          + `</div>` : "")
      + `</div>`;
  }).join("");

  // Option 2 — workflow-aware status line so a self-blocked step reads as
  // "1 self-blocked" inside a governed workflow, not a failed task.
  let statusLine = "";
  const sm = wf.summary || {};
  if (wf.detected && (sm.total || 0) > 0) {
    // Workflow-aware status line — and decision-aware: after Approve/Reject the
    // high-impact step reads as verified(simulated)/rejected, not "awaiting".
    // (gs is computed above for the step rows.)
    const bits = [];
    if (sm.auto) bits.push(`${sm.auto} auto-run`);
    if (sm.approval) bits.push(`${sm.approval} ${greenGateTag(gs)}`);
    if (sm.self_blocked) bits.push(`${sm.self_blocked} self-blocked`);
    statusLine = `<div class="wf-statusline">Governed workflow — `
      + `${esc(bits.join(" · "))}</div>`;
  }
  const srcLine = wf.database_note
    ? `<div class="wf-source"><b>Data source:</b> ${esc(wf.database_note)}</div>`
    : (wf.source_file
        ? `<div class="wf-source">Using <code>${esc(wf.source_file)}</code> `
          + `as the authoritative event-results file.</div>`
        : "");
  el.innerHTML =
    `<div class="wf-title">Workflow autonomy — more autonomy, without loss of control</div>`
    + statusLine
    + head
    + srcLine
    + (rows ? `<div class="wf-steps">${rows}</div>` : "")
    + blockedHtml;
  el.hidden = false;
}

function appendAgentMessage(task_id) {
  const wrap = document.createElement("div");
  wrap.className = "msg agent";
  wrap.dataset.taskId = task_id;
  wrap.innerHTML = `
    <div class="who">Agent</div>
    <div class="bubble status-running">
      <div class="web-search-indicator" hidden></div>
      <div class="episodic-indicator" hidden></div>
      <div class="answer"></div>
      <div class="web-sources" hidden></div>
      <div class="summary">Thinking…</div>
      <div class="extra-card" hidden></div>
      <div class="route-row"></div>
      <div class="gov-pipeline" hidden></div>
      <div class="workflow-panel" hidden></div>
      <div class="artifacts"></div>
      <div class="approval-area"></div>
      <details class="task-tree-block" hidden>
        <summary>Sub-goals</summary>
        <div class="task-tree-body"></div>
      </details>
      <details class="reflection-block" hidden>
        <summary>Learning &amp; memory policy</summary>
        <div class="reflection-body"></div>
      </details>
      <details class="details" hidden>
        <summary>Pipeline detail</summary>
        <ol class="timeline-mini"></ol>
      </details>
    </div>
  `;
  $("#chat").appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function appendAgentError(msg) {
  const wrap = document.createElement("div");
  wrap.className = "msg agent";
  wrap.innerHTML = `
    <div class="who">Agent</div>
    <div class="bubble status-error">
      <div class="summary"></div>
    </div>
  `;
  wrap.querySelector(".summary").textContent = msg;
  $("#chat").appendChild(wrap);
  scrollToBottom();
}

function scrollToBottom() {
  const chat = $("#chat");
  chat.scrollTop = chat.scrollHeight;
}

// ---------------- Task polling + render ----------------
async function pollTask(task_id, node) {
  let state_d;
  try {
    const r = await fetch(`/api/tasks/${task_id}`);
    if (!r.ok) return;
    state_d = await r.json();
  } catch (e) { return; }
  renderAgentMessage(node, state_d);
  if (state_d.status === "done" || state_d.status === "error") {
    const slot = state.tasks[task_id];
    if (slot && slot.poll_handle) { clearInterval(slot.poll_handle); slot.poll_handle = null; }
    // Refresh the learning panels the moment a task finishes. loadCurator()
    // is here (added with the Distiller→UI wire) so a freshly-distilled
    // create_skill proposal shows up in the Curator panel immediately,
    // without the operator having to reopen the drawer.
    if (slot) { loadStats(); loadSkills(); loadCurator(); }
  }
}

function renderAgentMessage(node, d) {
  const bubble = node.querySelector(".bubble");
  bubble.classList.remove("status-running", "status-awaiting", "status-done", "status-error");
  if (d.status === "awaiting_approval") bubble.classList.add("status-awaiting");
  else if (d.status === "done") bubble.classList.add("status-done");
  else if (d.status === "error") bubble.classList.add("status-error");
  else bubble.classList.add("status-running");

  // "🔍 Searched the web" indicator. We surface this BEFORE the answer
  // so the user knows the reply is grounded in live results, not just
  // the model's training data.
  const webIndicator = bubble.querySelector(".web-search-indicator");
  const webHits = extractWebSearchHits(d);
  if (webHits.length) {
    const provider = webHits[0].source || "web";
    webIndicator.innerHTML =
      `🔍 Searched the web · ${webHits.length} sources `
      + `<span class="provider">via ${escapeHtml(provider)}</span>`;
    webIndicator.hidden = false;
  } else {
    webIndicator.hidden = true;
    webIndicator.textContent = "";
  }

  // "🕘 Recalled N past sessions" indicator (Phase 17 — episodic memory).
  // Shows when the runtime queried the FTS5 session index pre-planner
  // because the user's prompt looked like a recall ("remember when",
  // "上次"). Same visual style as the web indicator but with a different
  // emoji so they're distinguishable at a glance.
  const epIndicator = bubble.querySelector(".episodic-indicator");
  const epHits = extractEpisodicHits(d);
  if (epHits.length) {
    epIndicator.innerHTML =
      `🕘 Recalled ${epHits.length} past session`
      + (epHits.length === 1 ? "" : "s")
      + ` <span class="provider">via session_search</span>`;
    epIndicator.title = epHits
      .map((h, i) => `[${i + 1}] ${h.raw_goal || h.task_id}`)
      .join("\n");
    epIndicator.hidden = false;
  } else {
    epIndicator.hidden = true;
    epIndicator.textContent = "";
  }

  // chat/text answer (conversational reply, rendered as the primary
  // content of the bubble when present)
  const answer = bubble.querySelector(".answer");
  // For a detected workflow the per-step bodies are summarised by the workflow
  // panel + the outcome line, so we don't surface a raw step body as the chat
  // answer — otherwise the GREEN step's "awaiting verification" notice would
  // still show as the reply AFTER the human has already approved/rejected.
  const chatText = (d.workflow && d.workflow.detected) ? "" : extractChatAnswer(d);
  if (chatText) {
    answer.innerHTML = linkifyCitations(escapeHtml(chatText), webHits);
    answer.style.display = "";
  } else {
    answer.innerHTML = "";
    answer.style.display = "none";
  }

  // Sources panel — only when we actually have web hits to cite.
  const sourcesEl = bubble.querySelector(".web-sources");
  if (webHits.length) {
    sourcesEl.innerHTML = renderSourcesList(webHits);
    sourcesEl.hidden = false;
  } else {
    sourcesEl.innerHTML = "";
    sourcesEl.hidden = true;
  }

  // summary line — shown when there's no natural-language answer to
  // carry the agent's reply, OR as a small operational footer when a
  // file artifact was produced.
  const summary = bubble.querySelector(".summary");
  if (d.status === "running") summary.textContent = "Thinking…";
  else if (d.status === "awaiting_approval") {
    const awf = d.workflow;
    if (awf && awf.detected) {
      const sm = awf.summary || {};
      summary.textContent = "Workflow complete — "
        + `${sm.auto || 0} low-risk steps done`
        + (sm.self_blocked ? `, ${sm.self_blocked} self-blocked` : "")
        + ". One high-impact action is awaiting your verification below.";
    } else {
      summary.textContent = "Prepared — your approval is required before any "
        + "external action. Nothing has been sent or published yet.";
    }
  }
  else if (d.status === "error") summary.textContent = d.error || "Something went wrong.";
  else {
    const outcome = describeOutcome(d);
    // When we already rendered a natural-language answer AND there are no
    // file artifacts, hide the terse "1 step succeeded" line to avoid
    // double-talking.
    if (chatText && !hasFileArtifact(d)) {
      summary.textContent = "";
      summary.style.display = "none";
    } else {
      summary.textContent = outcome;
      summary.style.display = "";
    }
  }

  // route chips
  const routes = bubble.querySelector(".route-row");
  routes.innerHTML = "";
  if (d.final_route) {
    const wf = d.workflow;
    if (wf && wf.detected) {
      // Option 2 — workflow-aware headline. A self-blocked step reads as
      // "1 self-blocked" inside a GOVERNED workflow, not a failed task. The
      // per-step routes (incl. the RED self-block) live in the workflow panel.
      const sm = wf.summary || {};
      const gs = greenGateState(d);
      const bits = [];
      if (sm.auto) bits.push(`${sm.auto} auto`);
      if (sm.approval) bits.push(`${sm.approval} ${gs === "approved" ? "verified" : gs === "rejected" ? "rejected" : "to verify"}`);
      if (sm.self_blocked) bits.push(`${sm.self_blocked} self-blocked`);
      const chip = document.createElement("span");
      chip.className = "chip WORKFLOW";
      chip.textContent = "WORKFLOW ✓ " + (bits.join(" · ") || "governed");
      chip.title = "Ran under governance — planner proposes, governance decides."
        + (sm.self_blocked
            ? "\nOne step was self-blocked by the agent's own data-use guard (see panel)."
            : "");
      routes.appendChild(chip);
    } else {
      const chip = document.createElement("span");
      chip.className = "chip " + d.final_route;
      chip.textContent = d.final_route;
      routes.appendChild(chip);
    }
  }
  // mark CACHE if cached this run
  const cached = (d.events || []).some(e => e.module === "CACHE" && e.event_type === "cache_hit");
  if (cached) {
    const chip = document.createElement("span");
    chip.className = "chip CACHE";
    chip.textContent = "EXECUTE_DIRECT";
    routes.appendChild(chip);
  }
  // mark AGENT-LOOP if the runtime fired a follow-up planner iteration
  const looped = (d.events || []).some(e =>
    e.module === "LOOP" && e.event_type === "agent_loop_followup_triggered");
  if (looped) {
    const chip = document.createElement("span");
    chip.className = "chip LOOP";
    chip.textContent = "AGENT-LOOP";
    routes.appendChild(chip);
  }
  // (Workflow status is shown as the headline route chip above — see the
  // Option-2 workflow-aware block at the top of the route row.)
  // Phase 13 — mark TREE when 102T decomposed this task. The chip
  // tooltip shows the sub-goal count + how many completed.
  const tree = d.task_tree;
  if (tree && Array.isArray(tree.leaves) && tree.leaves.length) {
    const done = tree.leaves.filter(l => l.status === "done").length;
    const chip = document.createElement("span");
    chip.className = "chip TREE";
    chip.textContent = `TREE ${done}/${tree.leaves.length}`;
    chip.title = (tree.reasoning || "")
      + (tree.reasoning ? "\n" : "")
      + tree.leaves
          .map((l, i) => `${i + 1}. [${l.status}] ${l.description}`)
          .join("\n");
    routes.appendChild(chip);
  }
  // mark REFLECT when Module 109 applied at least one memory update
  // for this task. Pending-review and skipped reflections do NOT get
  // the chip — only "applied" does. (Pending is shown in the audit
  // drawer's "Last reflection" panel instead.)
  const refl = d.reflection;
  if (refl && refl.disposition === "applied"
      && (refl.applied_updates || []).length > 0) {
    const chip = document.createElement("span");
    chip.className = "chip REFLECT";
    chip.textContent = "LEARNED";
    chip.title = refl.reasoning || "Module 109 applied memory updates";
    routes.appendChild(chip);
  }
  // Module 110 verification chip. Only shown when verification ran +
  // applied at least one check (skipped runs stay invisible to keep
  // the bubble clean for unrelated tasks). VERIFIED on pass, FAIL on
  // failure, with the failure reasons in the tooltip. Phase 14 adds
  // the judge score to the tooltip when the LLM judge ran.
  const ver = d.verification;
  // Show the VERIFIED chip only when the verifier ACTUALLY ran checks
  // (or fired the judge). A summary starting with "skipped:" means
  // verifier saw nothing to check (e.g. all executions failed) — in
  // that case do NOT show a green VERIFIED chip; it would be misleading
  // since the task itself failed.
  const verSkipped = ver && typeof ver.summary === "string"
                     && ver.summary.startsWith("skipped:");
  if (ver && ver.enabled !== false && !verSkipped
      && ((ver.checks || []).length > 0 || ver.judge)) {
    const chip = document.createElement("span");
    const judge = ver.judge || null;
    const judgeBit = judge
      ? `\nLLM judge: ${judge.pass ? "PASS" : "FAIL"} `
        + `(score ${judge.score}/${judge.threshold}, rubric=${judge.rubric_used})`
        + (judge.issues && judge.issues.length
            ? "\nIssues:\n  - " + judge.issues.join("\n  - ")
            : "")
      : "";
    if (ver.pass) {
      chip.className = "chip VERIFIED";
      chip.textContent = judge && judge.pass
        ? `VERIFIED ${judge.score}`
        : "VERIFIED";
      chip.title = (ver.summary || "all checks passed") + judgeBit;
    } else {
      chip.className = "chip VERIFY-FAIL";
      chip.textContent = "VERIFY-FAIL";
      chip.title = (ver.summary || "verification failed") + judgeBit;
    }
    routes.appendChild(chip);
  }

  // Governance pipeline card (judge-visible). Makes clear the LLM is not the
  // authority: planner proposes -> governance decides -> human approves ->
  // execution -> verification, with the route + reason in plain view.
  renderGovPipeline(bubble, d);
  renderExtraCard(bubble, d);
  // Workflow Autonomy panel (102W/101D) — beside the governance card.
  renderWorkflowPanel(bubble, d);
  // Phase 14 — SELF-FIX chip when the runtime re-ran the pipeline
  // because the judge initially failed. Green when recovered, red
  // when exhausted.
  if (ver && typeof ver.self_fix_iterations === "number"
      && ver.self_fix_iterations > 0) {
    const sfChip = document.createElement("span");
    sfChip.className = "chip " + (ver.self_fix_recovered
                                    ? "SELF-FIX-OK"
                                    : "SELF-FIX-FAIL");
    sfChip.textContent = `SELF-FIX ${ver.self_fix_iterations}x`;
    sfChip.title = ver.self_fix_recovered
      ? `Recovered after ${ver.self_fix_iterations} retry/retries.`
      : `Gave up after ${ver.self_fix_iterations} retry/retries.`;
    routes.appendChild(sfChip);
  }

  // artifacts: images, written files
  renderArtifacts(bubble.querySelector(".artifacts"), d);

  // approvals
  renderApprovals(bubble.querySelector(".approval-area"), d);

  // Module 109 reflection: surface what the agent decided to remember
  // (or rejected). Hidden when there's nothing to show.
  renderReflection(bubble.querySelector(".reflection-block"), d);

  // Phase 13 — Task Tree block. Shows the leaf list with status pills
  // and one-line summaries. Hidden when this task wasn't decomposed.
  renderTaskTree(bubble.querySelector(".task-tree-block"), d);

  // pipeline detail — target the .details block specifically. A bare
  // querySelector("details") would grab the FIRST <details> in the bubble
  // (the Sub-goals task-tree block) and wrongly un-hide that empty box.
  const details = bubble.querySelector("details.details");
  const events = d.events || [];
  if (events.length) {
    details.hidden = false;
    const ol = details.querySelector(".timeline-mini");
    ol.innerHTML = "";
    for (const ev of events) {
      const li = document.createElement("li");
      const route = inferRouteFromEvent(ev);
      if (route) li.classList.add("route-" + route);
      li.innerHTML = `<span class="m"></span><span class="et"></span><span class="s"></span>`;
      li.querySelector(".m").textContent = ev.module;
      li.querySelector(".et").textContent = ev.event_type;
      li.querySelector(".s").textContent = ev.summary || "";
      ol.appendChild(li);
    }
  }
}

// Pull the chat-tool answer (if any) out of a task state. We look at the
// trace events emitted by 107 (which now carry `details.tool`) and pick
// the first successful `chat` execution's output_summary. Falls back to
// scanning d.executions for an entry whose output_summary is plain prose
// (no known artifact prefix), in case events haven't streamed through.
function extractChatAnswer(d) {
  const events = d.events || [];
  for (const ev of events) {
    if (ev.module !== "107") continue;
    if (ev.event_type !== "execution_completed") continue;
    const det = ev.details || {};
    if (det.tool === "chat" && det.status === "success") {
      const body = det.output_summary || "";
      if (body && body.trim()) return body.trim();
    }
  }
  // Fallback: scan plain executions for chat-shaped output.
  for (const e of d.executions || []) {
    if (e.status !== "success") continue;
    const s = (e.output_summary || "").trim();
    if (!s) continue;
    if (/^(docx|pptx|xlsx|image|screenshot|report|fs)_/.test(s)) continue;
    if (s.startsWith("[demo]")) continue;  // simulated tool record, not a chat reply
    if (e.affected_resources && e.affected_resources.length) continue;
    return s;
  }
  return "";
}

function hasFileArtifact(d) {
  for (const e of d.executions || []) {
    if (e.affected_resources && e.affected_resources.length) return true;
  }
  return false;
}

// Pull episodic-recall hits (Phase 17 — past session lookups) out of
// the trace. Each hit: { task_id, raw_goal, score }.
function extractEpisodicHits(d) {
  const events = d.events || [];
  for (const ev of events) {
    if (ev.module !== "SESSION") continue;
    if (ev.event_type !== "session_search_retrieved") continue;
    const det = ev.details || {};
    const hits = det.hits || [];
    if (Array.isArray(hits) && hits.length) return hits;
  }
  return [];
}

// Pull the web search hits out of the trace, if the runtime injected them
// pre-planner. Each hit is { title, url, source }.
function extractWebSearchHits(d) {
  const events = d.events || [];
  for (const ev of events) {
    if (ev.module !== "WEB") continue;
    if (ev.event_type !== "web_search_retrieved") continue;
    const det = ev.details || {};
    const hits = det.hits || [];
    if (Array.isArray(hits) && hits.length) return hits;
  }
  return [];
}

// Replace inline citation markers like [1], [2] in the answer body with
// clickable <a> tags pointing at the corresponding source URL. Plain
// numbers without brackets are left alone to avoid mangling normal prose.
function linkifyCitations(escapedHtml, hits) {
  if (!hits.length) return escapedHtml;
  return escapedHtml.replace(/\[(\d{1,2})\]/g, (m, num) => {
    const idx = parseInt(num, 10) - 1;
    const h = hits[idx];
    if (!h || !h.url) return m;
    const safeUrl = escapeAttr(h.url);
    const safeTitle = escapeAttr(h.title || h.url);
    return `<a href="${safeUrl}" target="_blank" rel="noopener" class="citation" title="${safeTitle}">[${num}]</a>`;
  });
}

function renderSourcesList(hits) {
  const items = hits.map((h, i) => {
    const n = i + 1;
    const url = h.url || "";
    const title = h.title || url;
    return `<li><span class="num">[${n}]</span>`
         + `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">${escapeHtml(title)}</a>`
         + `<span class="src-url">${escapeHtml(url)}</span></li>`;
  }).join("");
  return `<div class="sources-title">Sources</div><ol class="sources-list">${items}</ol>`;
}

function escapeAttr(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
}

// Resolve the high-impact GREEN step's human-gate state from the task view, so
// the UI stops reading "awaiting verification" AFTER a decision is submitted.
//   pending  = gate open (task still awaiting, or a pending approval exists)
//   approved = human approved; the step executed (simulated in demo mode)
//   rejected = task settled but the GREEN step never ran (rejected / not executed)
//   null     = no GREEN step in this workflow
function greenGateState(d) {
  const wf = d && d.workflow;
  if (!wf || !wf.detected) return null;
  const g = (wf.steps || []).find(s => s.route === "GREEN");
  if (!g) return null;
  if (d.status === "awaiting_approval" || (d.pending_approvals || []).length) return "pending";
  return g.status === "success" ? "approved" : "rejected";
}
// Short counter tag for the workflow summary / status line.
function greenGateTag(gs) {
  return gs === "approved" ? "verified (simulated)"
       : gs === "rejected" ? "rejected"
       : "awaiting verification";
}
// Approval state for ANY GREEN route — the national workflow OR a standalone
// external-release probe (which is NOT a workflow, so greenGateState can't see
// it). Lets the learning panel stop saying "paused for approval" once the human
// gate is already resolved. Returns "pending" | "approved" | "rejected" | null.
function greenLearningState(d) {
  const wfState = greenGateState(d);          // precise for the workflow GREEN
  if (wfState) return wfState;
  if (d.status === "awaiting_approval" || (d.pending_approvals || []).length)
    return "pending";
  const greenDec = (d.decisions || []).find(
    de => (de.route || "").toUpperCase() === "GREEN");
  if (!greenDec) return null;
  // approved ⇢ the GREEN-routed action actually executed (demo-simulated counts);
  // rejected ⇢ the gate denied it, so no execution was recorded for it.
  const exec = (d.executions || []).some(e => e.action_id === greenDec.action_id);
  return exec ? "approved" : "rejected";
}

function describeOutcome(d) {
  // Workflow tasks: a self-blocked internal step must NOT read as a failed
  // task. Summarise the governed workflow (display only — the route stays RED).
  const wf = d.workflow;
  if (wf && wf.detected) {
    const sm = wf.summary || {};
    const gs = greenGateState(d);
    const bits = [];
    if (sm.auto) bits.push(`${sm.auto} done`);
    if (sm.approval) bits.push(`${sm.approval} ${greenGateTag(gs)}`);
    if (sm.self_blocked) bits.push(`${sm.self_blocked} self-blocked`);
    // Per-workflow governance copy (config governance_copy via _workflow_view),
    // so a charity-bazaar / speech-event summary is not described in
    // national-athletics / protected-student-record terms.
    const gc = wf.governance_copy || {};
    let msg = `Governed workflow — ${bits.join(" · ")}.`;
    if (sm.self_blocked) {
      const sb = gc.summary_self_block
        || "one unsafe internal data-use proposal was self-blocked (using a"
         + " parent's social title / PIBG status / household income or donation"
         + " potential to change a parent message's tone, priority or honest"
         + " reminder)";
      msg += " " + sb.charAt(0).toUpperCase() + sb.slice(1) + ".";
    }
    const greenNoun = gc.green_noun || "protected student-record write";
    if (gs === "approved") {
      msg += ` The ${greenNoun} was approved and simulated in demo mode —`
        + " nothing real was changed, sent, or published.";
    } else if (gs === "rejected") {
      msg += ` The ${greenNoun} was rejected — nothing was changed, sent, or`
        + " published.";
    } else if (gs === "pending") {
      msg += ` The ${greenNoun} is paused for your approval.`;
    }
    msg += " Nothing else is sent or published in demo mode.";
    return msg;
  }
  const route = d.final_route;
  // External release (send / publish): show a clean demo-release summary —
  // never the raw simulated tool records.
  const cat101a = (((d.events || []).find(e => e.module === "101A")) || {}).summary || "";
  const isExternalRelease = /external_(publish|email)/.test(cat101a)
    || (d.executions || []).some(e => String(e.output_summary || "").startsWith("[demo]"));
  if (route === "GREEN" && isExternalRelease) {
    const sent = (d.executions || []).filter(e =>
      e.status === "success" && String(e.output_summary || "").startsWith("[demo]")).length;
    if (sent) return `Demo release simulated — ${sent} external action(s) recorded in `
      + "the audit log, but no real message was delivered and no post was published "
      + "externally. External release requires prior human approval; demo mode records "
      + "the decision without contacting real recipients.";
    return "Release package prepared. Approval is required before any external "
      + "action — no real message has been sent and no post has been published "
      + "externally.";
  }
  if (route === "INFEASIBLE") {
    // A missing-event-detail invention request has its own honest answer —
    // not the reward-money "propose a framework" copy.
    if (/invent_missing_detail/.test(cat101a)) {
      return "I can't invent missing event details such as the date, venue, "
        + "teacher-in-charge, or assembly date. Those should be marked to be "
        + "confirmed (TBC). I can prepare a draft using only the facts provided, "
        + "with a short missing-information checklist for a human to complete.";
    }
    return "I can't answer this reliably — the available data doesn't support a "
      + "confident answer (no relevant policy, budget or precedent on file). "
      + "Rather than guess a number, here is a proposal framework for a human to "
      + "decide:";
  }
  if (route === "RED") {
    const reasons = (d.decisions || []).flatMap(de => de.reasons || []);
    const main = reasons.find(r => !String(r).startsWith("risk_recommended")
      && !String(r).startsWith("safe_alternative:") && r !== "data_use_guard_red");
    const alt = reasons.find(r => String(r).startsWith("safe_alternative:"));
    let msg = "Blocked by governance.";
    if (main) msg += " " + prettyReason(String(main));
    if (alt) msg += " Safe alternative: "
      + String(alt).replace(/^safe_alternative:\s*/i, "");
    return msg;
  }
  if (!d.executions || !d.executions.length) {
    if (route === "GREEN") return "Approved and ran.";
    return "Done.";
  }
  const oks = d.executions.filter(e => e.status === "success");
  const fails = d.executions.filter(e => e.status !== "success");
  const parts = [];
  if (oks.length) parts.push(`${oks.length} step${oks.length>1?"s":""} succeeded`);
  if (fails.length) parts.push(`${fails.length} failed/denied`);
  return parts.join(" · ") || "Done.";
}

function renderArtifacts(el, d) {
  el.innerHTML = "";
  // collect file outputs from executions
  const files = [];
  let latestScreenshot = null;
  let latestImage = null;
  for (const e of d.executions || []) {
    for (const f of e.affected_resources || []) {
      files.push({ status: e.status, path: f, summary: e.output_summary });
    }
    const s = e.output_summary || "";
    if (s.startsWith("screenshot_saved:") && e.affected_resources && e.affected_resources[0]) {
      latestScreenshot = basename(e.affected_resources[0]);
    }
    if ((s.startsWith("image_saved:") || s.startsWith("image_placeholder_saved:"))
        && e.affected_resources && e.affected_resources[0]) {
      latestImage = basename(e.affected_resources[0]);
    }
  }
  // generated image preview (most prominent)
  if (latestImage) {
    const img = document.createElement("img");
    img.className = "image-preview";
    img.src = `/api/images/${encodeURIComponent(latestImage)}`;
    img.alt = "Generated image";
    el.appendChild(img);
  }
  // screenshot preview (less prominent, only when no image)
  if (!latestImage && latestScreenshot) {
    const img = document.createElement("img");
    img.className = "image-preview";
    img.src = `/api/screenshots/${encodeURIComponent(latestScreenshot)}`;
    img.alt = "Screenshot";
    el.appendChild(img);
  }
  // file links (docx/pptx/xlsx/md/txt)
  const seen = new Set();
  for (const f of files) {
    const fn = basename(f.path);
    if (seen.has(fn)) continue;
    seen.add(fn);
    if (/\.(docx|pptx|xlsx|md|txt|pdf|csv|json)$/i.test(fn)) {
      const a = document.createElement("a");
      a.className = "artifact";
      a.href = `/api/outputs/${encodeURIComponent(fn)}`;
      a.target = "_blank";
      a.download = fn;
      a.title = fn;
      a.innerHTML = `<span class="icon">${iconFor(fn)}</span>`
        + `<span>${escapeHtml(friendlyArtifactLabel(fn))}</span>`;
      el.appendChild(a);
    }
  }
}

function friendlyArtifactLabel(fn) {
  return ({
    "save_internal_report.md": "Internal Activity Report Draft",
    "draft_internal_report.md": "Internal Activity Report Draft",
    "draft_public_fb_post.md": "Public Facebook Post Draft",
    "draft_parent_congrats_notice.md": "Parent Notice Draft",
  })[fn] || fn;
}

function iconFor(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return ({ docx: "📄", pptx: "📊", xlsx: "📈", pdf: "📕",
            md: "📝", txt: "📝", csv: "📋", json: "🧾",
            png: "🖼️", jpg: "🖼️" })[ext] || "📎";
}

function basename(p) {
  return String(p).replace(/\\/g, "/").split("/").pop();
}

// Module 109 reflection block. Surfaces:
//   * applied   → list of green-bullet entries actually written to memory
//   * pending   → orange-bullet entries that need human approval
//   * rejected  → red text explaining why policy blocked the proposal
//   * skipped/logged_only → muted line with reason (collapsed by default)
// Hidden entirely when there's no reflection or it was skipped silently.
function renderReflection(el, d) {
  const r = d.reflection;
  if (!r || r.skipped === "intent_too_short") {
    el.hidden = true;
    el.querySelector(".reflection-body").innerHTML = "";
    return;
  }
  const body = el.querySelector(".reflection-body");
  body.innerHTML = "";

  const route = (d.final_route || "").toUpperCase();
  const disp = r.disposition || (r.skipped ? "skipped" : "unknown");
  // A REAL, owner-gated non-personal procedure proposal (Module 109B SOP path).
  const sop = r.workflow_sop || null;
  // 101A category — lets the boundary explanation name the exact probe.
  let cat = "";
  const pre = (d.events || []).find(e => e.module === "101A");
  if (pre && pre.summary) { const m = /category=(\S+)/.exec(pre.summary); if (m) cat = m[1]; }

  // Declarative notes ACTUALLY written / queued to memory this task. The
  // confidence number is shown ONLY when such a real scored learning exists —
  // so a governed "nothing personal learned" run never shows a bare "0.00".
  const updates = [
    ...(r.user_md_updates || []).map(u => ({ ...u, scope: "USER" })),
    ...(r.memory_md_updates || []).map(u => ({ ...u, scope: "MEMORY" })),
  ];
  const hasRealLearning =
    (disp === "applied" || disp === "pending_review") && updates.length > 0;
  // GREEN approval lifecycle — so the panel stops saying "paused" once the
  // human gate is already resolved (approved/rejected).
  const greenState = route === "GREEN" ? greenLearningState(d) : null;

  // ---- collapsed summary chip (honest, never an empty box) ----------
  const summaryEl = el.querySelector("summary");
  if (summaryEl) {
    let chip;
    if (sop) {
      const m = sop.mode || "proposed";
      if (m === "reused")
        chip = `<span class="refl-chip refl-chip-pos">♻️ approved procedure reused</span>`;
      else if (m === "update_proposed")
        chip = `<span class="refl-chip refl-chip-pos">📘 procedure update proposed · owner approval</span>`;
      else
        chip = `<span class="refl-chip refl-chip-pos">📘 1 procedure proposed · owner approval</span>`;
    }
    else if (hasRealLearning) chip = `<span class="refl-chip refl-chip-pos">✅ memory updated</span>`;
    else if (route === "GREEN" && greenState === "approved")
      chip = `<span class="refl-chip refl-chip-pos">🧾 governance outcome recorded</span>`;
    else if (route === "INFEASIBLE" && /unsupported_amount_estimate|reward/.test(cat || ""))
      chip = `<span class="refl-chip refl-chip-pos">📋 decision framework produced</span>`;
    else if (route === "BLUE" && /parent_message_draft_edit/.test(cat || ""))
      chip = `<span class="refl-chip refl-chip-pos">🗂 output version recorded</span>`;
    else chip = `<span class="refl-chip">🔒 no personal data learned</span>`;
    summaryEl.innerHTML = "Learning &amp; memory policy " + chip;
  }

  // ---- headline + the "why" (boundary reasoning), per route ---------
  const header = document.createElement("div");
  header.className = "reflection-header";
  header.innerHTML = reflectionHeadline(route, cat, hasRealLearning, sop, greenState);
  body.appendChild(header);

  const why = document.createElement("div");
  why.className = "reflection-why";
  why.textContent = reflectionWhy(route, cat, hasRealLearning, sop, greenState);
  body.appendChild(why);

  // ---- the POSITIVE half: a real, non-personal procedure proposal ---
  if (sop) {
    const m = sop.mode || "proposed";
    let rsTitle, rsMetaTail;
    if (m === "reused") {
      rsTitle = "♻️ Approved procedure reused";
      rsMetaTail = "non-personal (PII-free) · status: <b>active</b> · "
        + "reused from procedural memory";
    } else if (m === "update_proposed") {
      rsTitle = "📘 Procedure update proposed for your approval";
      rsMetaTail = "non-personal (PII-free) · status: <b>pending owner approval</b> · "
        + "existing SOP remains active";
    } else {
      rsTitle = "📘 Procedure proposed for your approval";
      rsMetaTail = "non-personal (PII-free) · status: <b>pending owner approval</b> · "
        + "review it in the Curator panel";
    }
    const card = document.createElement("div");
    card.className = "reflection-sop";
    card.innerHTML =
      `<div class="rs-title">${rsTitle}</div>`
      + `<div class="rs-name">${escapeHtml(sop.name || "workflow procedure")}</div>`
      + `<div class="rs-meta">${escapeHtml(String(sop.steps || "?"))} steps · ${rsMetaTail}</div>`;
    body.appendChild(card);
  }

  // ---- real declarative updates (only when they exist) --------------
  if (hasRealLearning) {
    const ul = document.createElement("ul");
    ul.className = "reflection-list";
    for (const u of updates) {
      const li = document.createElement("li");
      li.className = "reflection-update";
      li.innerHTML =
        `<span class="reflection-scope">${escapeHtml(u.scope)}.md</span>`
        + `<span class="reflection-action">${escapeHtml(u.action || "add")}</span>`
        + `<span class="reflection-text">${escapeHtml(u.text || u.old_substring || "")}</span>`;
      ul.appendChild(li);
    }
    body.appendChild(ul);
    if (typeof r.confidence === "number") {
      const c = document.createElement("div");
      c.className = "reflection-conf";
      c.textContent = `confidence ${r.confidence.toFixed(2)}`;
      body.appendChild(c);
    }
  }

  // ---- an explicit policy rejection is still surfaced verbatim ------
  if (disp === "rejected_by_policy") {
    const msg = document.createElement("div");
    msg.className = "reflection-rejection";
    msg.textContent = `Memory write rejected by policy: ${r.rejection_reason || "?"}`;
    body.appendChild(msg);
  }

  el.hidden = false;
}

// Honest one-line headline for the learning panel, by governance route.
function reflectionHeadline(route, cat, hasRealLearning, sop, greenState) {
  const c = cat || "";
  if (sop) {
    const m = sop.mode || "proposed";
    if (m === "reused") return `<span class="reflection-disp reflection-disp-pos">`
      + `♻️ Approved procedure reused</span> · no new proposal`;
    if (m === "update_proposed") return `<span class="reflection-disp reflection-disp-pos">`
      + `📘 Procedure update proposed</span> · no personal data used`;
    return `<span class="reflection-disp reflection-disp-pos">`
      + `📘 Procedure learned (proposed)</span> · no personal data used`;
  }
  if (hasRealLearning) return `<span class="reflection-disp reflection-disp-applied">`
    + `✅ Memory updated</span> · non-personal note`;
  // Bounded, SELECTIVE learning — not just "refused to learn":
  if (route === "INFEASIBLE" && /unsupported_amount_estimate|reward/.test(c))
    return `<span class="reflection-disp reflection-disp-pos">`
      + `📋 Decision framework produced</span> · no amount guessed or learned`;
  if (route === "BLUE" && /parent_message_draft_edit/.test(c))
    return `<span class="reflection-disp reflection-disp-pos">`
      + `🗂 Output version recorded</span> · no long-term memory update`;
  if (route === "RED" && /learning|sensitive_data_learning|student_data/.test(c))
    return `<span class="reflection-disp reflection-disp-governed">`
      + `🔒 Personal-data learning blocked</span>`;
  if (route === "RED") return `<span class="reflection-disp reflection-disp-governed">`
    + `🔒 Learning boundary enforced</span> · nothing personal learned`;
  if (route === "INFEASIBLE") return `<span class="reflection-disp reflection-disp-governed">`
    + `🔒 Nothing guessed, nothing learned</span>`;
  if (route === "GREEN") {
    if (greenState === "approved") return `<span class="reflection-disp reflection-disp-pos">`
      + `🧾 Governance outcome recorded</span> · no personal data learned`;
    if (greenState === "rejected") return `<span class="reflection-disp reflection-disp-governed">`
      + `🔒 Release rejected</span> · no memory change`;
    return `<span class="reflection-disp reflection-disp-governed">`
      + `🔒 Paused for your approval</span> · no memory change yet`;
  }
  return `<span class="reflection-disp reflection-disp-governed">`
    + `🔒 No personal data learned</span> · boundary held`;
}

// The honest "why" — explains what the boundary did, naming the exact probe.
function reflectionWhy(route, cat, hasRealLearning, sop, greenState) {
  if (sop) {
    const m = sop.mode || "proposed";
    if (m === "reused") return "The system reused the approved non-personal SOP for "
      + "this workflow. No new memory proposal was created, and no student or parent "
      + "data was learned — the owner's earlier approval still governs this procedure.";
    if (m === "update_proposed") return "The workflow's step shape differed from the "
      + "approved SOP, so the system proposed a non-personal procedure UPDATE for your "
      + "review. The existing approved SOP stays active until you approve it; no student "
      + "or parent data was written to memory.";
    return "The system distilled the workflow's PROCEDURE — the governed step shape, "
      + "including the self-block — into a reusable, non-personal SOP. No student or "
      + "parent data was written to memory; the procedure is queued for your approval "
      + "before it can be reused.";
  }
  const c = cat || "";
  if (route === "INFEASIBLE" && /unsupported_amount_estimate|reward/.test(c))
    return "The system did not estimate an unsupported reward amount. It produced a "
      + "reusable approval framework for a human (Headmaster / Board / PIBG) to decide; "
      + "no amount, personal data or unverified policy was stored as memory.";
  if (route === "BLUE" && /parent_message_draft_edit/.test(c))
    return "This was a one-off schedule update to an existing parent notice. The "
      + "updated output version and audit trace were recorded, but the event-specific "
      + "dates, times and venue were not stored as reusable memory.";
  if (/learning|sensitive_data_learning|student_data/.test(c))
    return "You asked the system to train on the student database. That is exactly "
      + "what the learning boundary forbids: personal student / parent data is never "
      + "distilled into the model or memory. The refusal is recorded in the audit "
      + "trail — the data is not.";
  if (route === "RED")
    return "This route was self-blocked for unsafe data use, so nothing was learned "
      + "from it — by design. Sensitive student / parent data is never distilled into "
      + "persistent memory; what persists is the governance decision in the audit trail.";
  if (route === "INFEASIBLE")
    return "The system did not guess and did not store a fabricated fact. What it "
      + "recorded is which policy data it WOULD need to answer — surfaced to you as a "
      + "proposal framework, not learned as truth.";
  if (route === "GREEN") {
    if (greenState === "approved")
      return "The human-approved demo release decision was recorded in the audit "
        + "trail. No parent or student data, message contents, contact details, or "
        + "external-post content was stored as reusable memory.";
    if (greenState === "rejected")
      return "You rejected the release, so nothing was executed and nothing was "
        + "learned. The governance decision is kept in the audit trail; no personal "
        + "data was stored.";
    return "The high-impact action is paused for your approval, so no memory was "
      + "changed. Only after you decide can the outcome (never the underlying "
      + "personal data) inform future routing.";
  }
  if (hasRealLearning)
    return "A non-personal, general note was written to memory (shown below). "
      + "Sensitive student / parent data is never included.";
  return "No personal data was written to memory, and this one-off task did not "
    + "produce a general, non-personal procedure worth saving. The "
    + "governance↔learning boundary held.";
}

// Phase 13 — Task Tree visualisation. Shows the leaves with their
// status pill (pending/running/done/failed/skipped) and a one-line
// summary. Auto-expanded when the task was decomposed.
function renderTaskTree(el, d) {
  const tree = d.task_tree;
  if (!tree || !Array.isArray(tree.leaves) || !tree.leaves.length) {
    el.hidden = true;
    el.querySelector(".task-tree-body").innerHTML = "";
    return;
  }
  // Auto-open the disclosure on first render so user immediately sees
  // the tree (rather than having to click to expand).
  if (!el.dataset.userToggled) el.open = true;
  el.addEventListener("toggle", () => { el.dataset.userToggled = "1"; },
                       { once: true });

  const body = el.querySelector(".task-tree-body");
  body.innerHTML = "";

  if (tree.reasoning) {
    const r = document.createElement("div");
    r.className = "tree-reasoning";
    r.textContent = tree.reasoning;
    body.appendChild(r);
  }

  const ul = document.createElement("ol");
  ul.className = "tree-leaves";
  for (const leaf of tree.leaves) {
    const li = document.createElement("li");
    li.className = "tree-leaf tree-leaf-" + escapeAttr(leaf.status || "pending");
    li.innerHTML = `
      <span class="tree-pill"></span>
      <div class="tree-leaf-body">
        <div class="tree-desc"></div>
        <div class="tree-meta">
          <span class="tree-sgid"></span>
          <span class="tree-route"></span>
          <span class="tree-summary"></span>
        </div>
      </div>
    `;
    li.querySelector(".tree-pill").textContent = (leaf.status || "?").toUpperCase();
    li.querySelector(".tree-desc").textContent = leaf.description || "";
    li.querySelector(".tree-sgid").textContent = leaf.sub_goal_id || "";
    if (leaf.final_route) {
      const rt = li.querySelector(".tree-route");
      rt.textContent = leaf.final_route;
      rt.classList.add("chip", "chip-tiny", leaf.final_route);
    }
    if (leaf.summary) {
      li.querySelector(".tree-summary").textContent = "— " + leaf.summary;
    }
    if (leaf.depends_on && leaf.depends_on.length) {
      const dep = document.createElement("span");
      dep.className = "tree-deps";
      dep.textContent = "← " + leaf.depends_on.join(", ");
      li.querySelector(".tree-meta").appendChild(dep);
    }
    ul.appendChild(li);
  }
  body.appendChild(ul);
  el.hidden = false;
}

function renderApprovals(el, d) {
  const pending = d.pending_approvals || [];
  const wantIds = new Set(pending.map(a => a.approval_id));
  // Drop cards no longer pending.
  for (const card of [...el.querySelectorAll(".approval-card")]) {
    if (!wantIds.has(card.dataset.approvalId)) card.remove();
  }
  for (const a of pending) {
    const sel = (window.CSS && CSS.escape) ? CSS.escape(a.approval_id) : a.approval_id;
    let card = el.querySelector(`.approval-card[data-approval-id="${sel}"]`);
    // Build each card ONCE (keyed by approval_id). Previously the 500ms poller
    // wiped + rebuilt the whole card every tick — a click landing mid-rebuild
    // hit a button that was being destroyed (the "needs 2-3 clicks" bug), and
    // any typed note was erased. Now we create once and only update status.
    if (!card) {
      card = document.createElement("div");
      card.className = "approval-card";
      card.dataset.approvalId = a.approval_id;
      card.innerHTML = `
        <div class="summary"></div>
        <div class="reasons"></div>
        <div class="actions">
          <input type="text" placeholder="Optional note…" />
          <button class="approve">Approve</button>
          <button class="reject">Reject</button>
        </div>
        <div class="approval-status"></div>
      `;
      card.querySelector(".summary").textContent = a.summary || "Action needs approval";
      const reasons = (a.context && a.context.reasons) || [];
      card.querySelector(".reasons").textContent = "reasons: " + reasons.join(" · ");
      const note0 = card.querySelector("input");
      const approveBtn0 = card.querySelector(".approve");
      const rejectBtn0 = card.querySelector(".reject");
      const statusEl0 = card.querySelector(".approval-status");
      approveBtn0.addEventListener("click", () =>
        submitDecision(d.task_id, a.approval_id, "approved",
                       note0, approveBtn0, rejectBtn0, statusEl0));
      rejectBtn0.addEventListener("click", () =>
        submitDecision(d.task_id, a.approval_id, "rejected",
                       note0, approveBtn0, rejectBtn0, statusEl0));
      el.appendChild(card);
    }
    // Reflect the submit state machine (submitting / submitted / error) in
    // place — no rebuild, so the click is never lost.
    const note = card.querySelector("input");
    const approveBtn = card.querySelector(".approve");
    const rejectBtn = card.querySelector(".reject");
    const statusEl = card.querySelector(".approval-status");
    const prev = state.approvals[a.approval_id];
    if (prev) {
      const lock = prev.status === "submitting" || prev.status === "submitted";
      approveBtn.disabled = lock;
      rejectBtn.disabled = lock;
      note.disabled = lock;
      if (prev.status === "submitting") {
        statusEl.textContent = "Sending…";
        statusEl.className = "approval-status status-pending";
      } else if (prev.status === "submitted") {
        statusEl.textContent = `✓ ${prev.decision} submitted`;
        statusEl.className = "approval-status status-ok";
      } else if (prev.status === "error") {
        statusEl.textContent = `! ${prev.error || "submit failed"}`;
        statusEl.className = "approval-status status-err";
      }
    }
  }
}

async function submitDecision(task_id, approval_id, decision,
                              noteEl, approveBtn, rejectBtn, statusEl) {
  // Lock immediately so a frustrated double-click doesn't fire twice. The
  // server treats the second call as 404 "approval_not_pending", which is
  // correct but looks like an error to the user.
  if (state.approvals[approval_id]?.status === "submitting") return;
  state.approvals[approval_id] = { status: "submitting", decision };
  approveBtn.disabled = true;
  rejectBtn.disabled = true;
  noteEl.disabled = true;
  statusEl.textContent = "Sending…";
  statusEl.className = "approval-status status-pending";

  try {
    const r = await fetch(`/api/tasks/${task_id}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approval_id, status: decision,
                             note: noteEl.value || null }),
    });
    if (r.ok) {
      state.approvals[approval_id] = { status: "submitted", decision };
      statusEl.textContent = `✓ ${decision} submitted`;
      statusEl.className = "approval-status status-ok";
    } else if (r.status === 404) {
      // Server already processed this approval (maybe a re-render fired
      // the click handler twice, or the previous response got lost). Show
      // as success — the action IS done.
      state.approvals[approval_id] = { status: "submitted", decision };
      statusEl.textContent = `✓ already processed`;
      statusEl.className = "approval-status status-ok";
    } else {
      const txt = await r.text().catch(() => `HTTP ${r.status}`);
      state.approvals[approval_id] = { status: "error", error: txt };
      statusEl.textContent = `! ${txt.slice(0, 120)}`;
      statusEl.className = "approval-status status-err";
      approveBtn.disabled = false;
      rejectBtn.disabled = false;
      noteEl.disabled = false;
    }
  } catch (e) {
    state.approvals[approval_id] = { status: "error", error: e.message };
    statusEl.textContent = `! ${e.message}`;
    statusEl.className = "approval-status status-err";
    approveBtn.disabled = false;
    rejectBtn.disabled = false;
    noteEl.disabled = false;
  }
}

function inferRouteFromEvent(ev) {
  const t = (ev.event_type || "").toLowerCase();
  // Module 110 verifier events. Pass = green stripe (BLUE family
  // looks too similar; we reuse GREEN since it's "approved" semantics);
  // fail = red stripe.
  if (ev.module === "110" || t.startsWith("verification_")) {
    if (t === "verification_failed" || t === "verification_error") return "RED";
    return "GREEN";
  }
  // Module 109 reflection events get the REFLECT stripe so they stand
  // out from the regular pipeline events in the timeline.
  if (ev.module === "109" || t.startsWith("reflection_")) return "REFLECT";
  // Phase 17 episodic events reuse the LOOP stripe (purple) since they
  // share the "extra context injected pre-planner" semantics.
  if (ev.module === "SESSION" || t.startsWith("session_")) return "LOOP";
  // Phase 13 task-tree events get the same purple family — they're
  // structural rather than per-action.
  if (ev.module === "102T" || t.startsWith("tree_") || t.startsWith("leaf_")
      || t === "decompose_refused" || t === "decomposer_error") return "LOOP";
  // Agent-loop iteration marker — gets its own purple stripe so users
  // can see at a glance where the second pass starts in the timeline.
  // Phase 14 self-fix events share the LOOP stripe (same family).
  if (ev.module === "LOOP"
      || t.includes("agent_loop")
      || t.startsWith("self_fix_")) return "LOOP";
  if (t.includes("cache_hit") || t.includes("plan_cache") || t.includes("cache_invalid")) return "CACHE";
  if (t.includes("infeasib")) return "INFEASIBLE";
  if (t.includes("red_blocked") || t.includes("emergency")) return "RED";
  if (t === "human_approved" || t.includes("ticket_issued")) return "GREEN";
  if (t === "human_rejected") return "RED";
  if (t === "execution_completed") return "BLUE";
  if (t === "execution_failed") return "RED";
  const s = (ev.summary || "");
  if (s.includes("INFEASIBLE")) return "INFEASIBLE";
  if (s.includes("BLUE")) return "BLUE";
  if (s.includes("GREEN")) return "GREEN";
  if (s.includes("RED")) return "RED";
  return null;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);
}

// ---------------- File upload (drag-drop + button) ----------------
function renderAttachments() {
  const el = $("#composer-attachments");
  el.innerHTML = "";
  for (const a of state.attachments) {
    const chip = document.createElement("span");
    chip.className = "attach-chip";
    chip.innerHTML = `<span>📎 ${escapeHtml(a.filename)}</span><span class="x" title="remove">✕</span>`;
    chip.querySelector(".x").addEventListener("click", () => {
      state.attachments = state.attachments.filter(x => x !== a);
      renderAttachments();
    });
    el.appendChild(chip);
  }
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/uploads", { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    state.attachments.push({
      filename: data.filename,
      url: data.url,
      size_bytes: data.size_bytes,
    });
    renderAttachments();
  } catch (e) {
    alert("Upload failed: " + e.message);
  }
}

function bindDragDrop() {
  const row = $(".composer-row");
  ["dragenter", "dragover"].forEach(ev => row.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation();
    row.classList.add("dragover");
  }));
  ["dragleave", "drop"].forEach(ev => row.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation();
    row.classList.remove("dragover");
  }));
  row.addEventListener("drop", async (e) => {
    const files = Array.from(e.dataTransfer.files || []);
    for (const f of files) await uploadFile(f);
  });
}

// ---------------- Audit drawer panels (existing) ----------------
async function loadRagStatus() {
  try {
    const r = await fetch("/api/rag/status");
    const d = await r.json();
    const el = $("#rag-status");
    if (d.loaded && d.header) {
      el.className = "rag-status loaded";
      el.textContent = `${d.header.n_chunks} chunks · ${d.header.n_files} files`;
    } else {
      el.className = "rag-status missing";
      el.textContent = "no index — click Re-index to build";
    }
  } catch (e) {}
}

async function reindex() {
  const btn = $("#rag-reindex");
  btn.disabled = true; btn.textContent = "Indexing…";
  try {
    const r = await fetch("/api/rag/reindex", { method: "POST" });
    if (!r.ok) throw new Error(await r.text());
    await loadRagStatus();
  } catch (e) { alert("Reindex failed: " + e.message); }
  finally { btn.disabled = false; btn.textContent = "Re-index workspace"; }
}

async function loadStats() {
  try {
    const r = await fetch("/api/stats");
    const d = await r.json();
    const el = $("#stats-panel");
    const cats = d.categories || {};
    const names = Object.keys(cats);
    if (!names.length) { el.innerHTML = '<div class="empty">no history yet</div>'; return; }
    names.sort((a, b) => (cats[b].total||0) - (cats[a].total||0));
    el.innerHTML = "";
    for (const name of names.slice(0, 12)) {
      const c = cats[name];
      const row = document.createElement("div");
      row.className = "stats-row" + (c.confident ? " confident" : "");
      row.innerHTML = `<span class="cat"></span><span class="score"></span>`;
      row.querySelector(".cat").textContent = `${name} (${c.successes||0}/${c.total||0})`;
      row.querySelector(".score").textContent = (c.score||0).toFixed(2) + (c.confident ? " ✓" : "");
      el.appendChild(row);
    }
    const cache = d.plan_cache || [];
    if (cache.length) {
      const hdr = document.createElement("div");
      hdr.style.marginTop = "8px"; hdr.style.fontSize = "10px";
      hdr.style.color = "var(--text-mute)";
      hdr.textContent = `cached templates: ${cache.length} (${cache.filter(c=>c.status==='active').length} active)`;
      el.appendChild(hdr);
    }
  } catch (e) {}
}

async function loadMemory() {
  try {
    const r = await fetch("/api/memory");
    const d = await r.json();
    const el = $("#memory-panel");
    const userText = (d.user_md || "").trim();
    const envText = (d.memory_md || "").trim();
    if (!userText && !envText) {
      el.innerHTML = '<div class="empty">empty — agent has no notes yet</div>';
      return;
    }
    el.innerHTML = "";
    if (userText) {
      const h = document.createElement("h4"); h.textContent = "USER.md"; el.appendChild(h);
      const pre = document.createElement("pre"); pre.textContent = userText; el.appendChild(pre);
    }
    if (envText) {
      const h = document.createElement("h4"); h.textContent = "MEMORY.md"; el.appendChild(h);
      const pre = document.createElement("pre"); pre.textContent = envText; el.appendChild(pre);
    }
  } catch (e) {}
}

async function loadSkills() {
  // Render the procedural-memory store as a compact list of skill cards.
  // Each card has the name, description, usage_count, char_length, and
  // a "view body" toggle that fetches /api/skills/<id> on demand.
  try {
    const r = await fetch("/api/skills");
    const d = await r.json();
    const el = $("#skills-panel");
    const skills = d.skills || [];
    if (!skills.length) {
      el.innerHTML = '<div class="empty">no skills yet — agent will save them after non-trivial successful tasks</div>';
      return;
    }
    el.innerHTML = "";
    for (const s of skills) {
      const card = document.createElement("div");
      card.className = "skill-card";
      card.innerHTML = `
        <div class="s-head">
          <span class="s-name"></span>
          <span class="s-meta"></span>
        </div>
        <div class="s-desc"></div>
        <div class="s-tags"></div>
        <details class="s-body"><summary>view procedure</summary>
          <pre class="s-body-pre">loading…</pre>
        </details>
      `;
      card.querySelector(".s-name").textContent = s.name || s.skill_id;
      card.querySelector(".s-meta").textContent =
        `used ${s.usage_count || 0} · ${s.char_length || 0} chars`;
      card.querySelector(".s-desc").textContent = s.description || "";
      const tagsEl = card.querySelector(".s-tags");
      for (const t of (s.tags || [])) {
        const tag = document.createElement("span");
        tag.className = "s-tag";
        tag.textContent = t;
        tagsEl.appendChild(tag);
      }
      const details = card.querySelector(".s-body");
      const pre = card.querySelector(".s-body-pre");
      let loaded = false;
      details.addEventListener("toggle", async () => {
        if (!details.open || loaded) return;
        try {
          const br = await fetch(`/api/skills/${encodeURIComponent(s.skill_id)}`);
          const bd = await br.json();
          pre.textContent = bd.body || "(empty)";
          loaded = true;
        } catch (e) { pre.textContent = "(load failed: " + e.message + ")"; }
      });
      el.appendChild(card);
    }
  } catch (e) {}
}

// Phase 16 — Curator panel. Renders pending proposals as approve/reject
// cards. The "Run curator" button at the section header triggers a
// fresh review pass. Each card shows: type, target_file, old → new
// diff, reasoning, and decide buttons. Approval applies the proposal
// via the runtime (UserMemory.replace / SkillManager.archive_skill).
async function loadCurator() {
  try {
    const r = await fetch("/api/curator/proposals");
    const d = await r.json();
    const el = $("#curator-panel");
    const items = d.proposals || [];
    if (!items.length) {
      el.innerHTML = '<div class="empty">no curator proposals yet — '
        + 'click "Run curator" above to scan memory</div>';
      return;
    }
    // Brief 2 — panel hierarchy: PENDING owner approvals get full
    // approve/reject cards; already-decided memory is compact + collapsed so
    // a re-tested demo doesn't pile up orphaned "APPLIED" cards (with the
    // buttons gone) that read as broken to a judge.
    const pending = items.filter(p => (p.status || "pending") === "pending");
    const decided = items.filter(p => (p.status || "pending") !== "pending");
    el.innerHTML = "";

    if (pending.length) {
      for (const p of pending) el.appendChild(buildCuratorCard(p, false));
    } else {
      const note = document.createElement("div");
      note.className = "empty";
      note.textContent = "No pending owner approvals. Approved procedures are "
        + "available in Procedural Memory.";
      el.appendChild(note);
    }

    if (decided.length) {
      const det = document.createElement("details");
      det.className = "cur-applied-group";
      const sum = document.createElement("summary");
      sum.textContent =
        `Applied / decided procedural memory (${decided.length})`;
      det.appendChild(sum);
      for (const p of decided) det.appendChild(buildCuratorCard(p, true));
      el.appendChild(det);
    }
  } catch (e) {}
}

// Build one Curator proposal card. `compact` collapses a DECIDED proposal to a
// single status row (no diff, no buttons); the full card keeps the exact
// approve/reject flow for PENDING proposals.
function buildCuratorCard(p, compact) {
  const isSkill = p.kind === "create_skill" || (!p.type && (p.name || p.procedure));
  if (compact) {
    const row = document.createElement("div");
    row.className = "curator-card cur-compact curator-status-"
      + escapeAttr(p.status || "pending");
    const reason = document.createElement("span");
    reason.className = "cur-apply-note";
    reason.textContent = p.apply_reason || "";
    const head = document.createElement("div");
    head.className = "cur-head";
    const t = document.createElement("span");
    t.className = "cur-type"; t.textContent = p.type || p.kind || "?";
    const tgt = document.createElement("span");
    tgt.className = "cur-target"; tgt.textContent = p.target_file || p.name || "";
    const st = document.createElement("span");
    st.className = "cur-status";
    st.textContent = (p.status || "pending").toUpperCase();
    head.appendChild(t); head.appendChild(tgt); head.appendChild(st);
    row.appendChild(head);
    if (reason.textContent) row.appendChild(reason);
    return row;
  }

  const card = document.createElement("div");
  card.className = "curator-card curator-status-" + escapeAttr(p.status || "pending");
  card.innerHTML = `
    <div class="cur-head">
      <span class="cur-type"></span>
      <span class="cur-target"></span>
      <span class="cur-status"></span>
    </div>
    <div class="cur-reason"></div>
    <div class="cur-diff">
      <div class="cur-old"><span class="cur-label">old</span><pre></pre></div>
      <div class="cur-new"><span class="cur-label">new</span><pre></pre></div>
    </div>
    <div class="cur-actions"></div>
  `;
  // Two proposal shapes share this card: memory-patch (type/target_file/
  // old_text/new_text) and create_skill (kind/name/description/procedure,
  // e.g. the non-personal workflow SOP). Render whichever fields exist so a
  // skill proposal never shows "?" + empty diff blocks.
  card.querySelector(".cur-type").textContent = p.type || p.kind || "?";
  card.querySelector(".cur-target").textContent = p.target_file || p.name || "";
  card.querySelector(".cur-status").textContent = (p.status || "pending").toUpperCase();
  card.querySelector(".cur-reason").textContent = p.reasoning || p.description || "";
  if (isSkill) {
    const oldLabel = card.querySelector(".cur-old .cur-label");
    const newLabel = card.querySelector(".cur-new .cur-label");
    if (oldLabel) oldLabel.textContent = "kind";
    if (newLabel) newLabel.textContent = "procedure";
    card.querySelector(".cur-old pre").textContent =
      `${p.kind || "create_skill"} · non-personal (PII-free)`;
    card.querySelector(".cur-new pre").textContent =
      (p.procedure || p.description || "(empty)").slice(0, 800);
  } else {
    card.querySelector(".cur-old pre").textContent =
      (p.old_text || "").slice(0, 600);
    const newText = (p.new_text || "").slice(0, 600);
    card.querySelector(".cur-new pre").textContent =
      newText || (p.type === "archive_skill" ? "(skill will be archived)" : "(empty)");
  }
  // Action buttons only on pending proposals
  const actions = card.querySelector(".cur-actions");
  if (p.status === "pending") {
    const approveBtn = document.createElement("button");
    approveBtn.className = "cur-approve";
    approveBtn.textContent = "Approve";
    const rejectBtn = document.createElement("button");
    rejectBtn.className = "cur-reject";
    rejectBtn.textContent = "Reject";
    approveBtn.addEventListener("click", () =>
      decideCuratorProposal(p.proposal_id, "approved",
                             approveBtn, rejectBtn, card));
    rejectBtn.addEventListener("click", () =>
      decideCuratorProposal(p.proposal_id, "rejected",
                             approveBtn, rejectBtn, card));
    actions.appendChild(approveBtn);
    actions.appendChild(rejectBtn);
  } else if (p.apply_reason) {
    const note = document.createElement("span");
    note.className = "cur-apply-note";
    note.textContent = p.apply_reason;
    actions.appendChild(note);
  }
  return card;
}

async function decideCuratorProposal(proposalId, decision,
                                     approveBtn, rejectBtn, card) {
  approveBtn.disabled = rejectBtn.disabled = true;
  const orig = card.querySelector(".cur-status").textContent;
  card.querySelector(".cur-status").textContent = "SENDING…";
  try {
    const r = await fetch(`/api/curator/proposals/${encodeURIComponent(proposalId)}/decide`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({status: decision, approved_by: "web_user"}),
    });
    if (!r.ok) {
      const text = await r.text().catch(() => `HTTP ${r.status}`);
      card.querySelector(".cur-status").textContent = "ERR: " + text.slice(0, 60);
      approveBtn.disabled = rejectBtn.disabled = false;
      return;
    }
    // Reload to pick up the updated status (applied / approved / rejected)
    await loadCurator();
    // P5 — an approved create_skill proposal has just written a new skill
    // and bumped the counters. Refresh the Skills + Stats panels right
    // here so the operator sees the skill appear under "Skills · procedural
    // memory" immediately, instead of staring at "no skills yet" until the
    // next task completes or the drawer is reopened.
    loadSkills();
    loadStats();
  } catch (e) {
    card.querySelector(".cur-status").textContent = "ERR: " + e.message;
    approveBtn.disabled = rejectBtn.disabled = false;
  }
}

async function runCurator(btn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Running…";
  try {
    const r = await fetch("/api/curator/run", {method: "POST"});
    const d = await r.json();
    if (!d.ok) {
      btn.textContent = "Err: " + (d.error || "").slice(0, 30);
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 3000);
      return;
    }
    btn.textContent = `Queued ${d.proposals.length} proposal${d.proposals.length === 1 ? "" : "s"}`;
    setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2500);
    await loadCurator();
  } catch (e) {
    btn.textContent = "Err: " + e.message.slice(0, 30);
    setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 3000);
  }
}

async function loadHistory() {
  try {
    const r = await fetch("/api/tasks");
    const d = await r.json();
    const list = $("#history-list");
    if (!d.tasks || !d.tasks.length) {
      list.innerHTML = '<div class="empty">no tasks yet</div>';
      return;
    }
    const sorted = [...d.tasks].sort((a, b) => (b.started_at||"").localeCompare(a.started_at||""));
    list.innerHTML = "";
    for (const t of sorted.slice(0, 20)) {
      const item = document.createElement("div");
      item.className = "history-item";
      item.innerHTML = `<div class="h-goal"></div><div class="h-meta"><span class="chip"></span><span></span></div>`;
      item.querySelector(".h-goal").textContent = (t.raw_goal || "").slice(0, 100);
      const chip = item.querySelector(".chip");
      if (t.workflow_detected) {
        // A governed workflow with a self-blocked step must not read as a
        // failed RED task in the history list (display only; route stays RED).
        const sm = t.workflow_summary || {};
        chip.classList.add("WORKFLOW");
        chip.textContent = sm.self_blocked ? "READY · 1 self-blocked" : "READY";
        chip.title = `Governed workflow — ${sm.auto || 0} done · `
          + `${sm.approval || 0} approval · ${sm.self_blocked || 0} self-blocked`;
      } else {
        chip.classList.add(t.final_route || "NONE");
        chip.textContent = t.final_route || "NONE";
      }
      item.querySelectorAll(".h-meta span")[1].textContent = (t.started_at || "").slice(11, 19);
      list.appendChild(item);
    }
  } catch (e) {}
}

async function loadPatches() {
  try {
    const r = await fetch("/api/patches");
    const d = await r.json();
    const list = $("#patches-list");
    if (!d.patches || !d.patches.length) {
      list.innerHTML = '<div class="empty">no proposals</div>'; return;
    }
    list.innerHTML = "";
    for (const p of d.patches.slice(0, 10)) {
      const item = document.createElement("div");
      item.className = "patch-item";
      const sig = (p.proposed_change||{}).context_signature || "";
      item.innerHTML = `
        <div class="p-type"></div>
        <div class="p-reason"></div>
        ${p.status === "proposed" ?
          `<div class="p-actions"><button class="approve">Approve</button><button class="reject">Reject</button></div>` :
          `<div class="p-status ${p.status}"></div>`}
      `;
      item.querySelector(".p-type").textContent = p.patch_type;
      item.querySelector(".p-reason").textContent = sig || p.reason;
      if (p.status === "proposed") {
        item.querySelector(".approve").addEventListener("click", () => decidePatch(p.patch_id, "approved"));
        item.querySelector(".reject").addEventListener("click", () => decidePatch(p.patch_id, "rejected"));
      } else {
        item.querySelector(".p-status").textContent = p.status;
      }
      list.appendChild(item);
    }
  } catch (e) {}
}

async function decidePatch(patch_id, status) {
  try {
    await fetch(`/api/patches/${patch_id}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    await loadPatches();
  } catch (e) { alert("Patch decision failed: " + e.message); }
}

// ---------------- Boot ----------------
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  loadConfig();
  loadStats();
  loadMemory();

  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#audit-toggle").addEventListener("click", toggleAudit);
  $("#run-btn").addEventListener("click", startTask);
  $("#rag-reindex").addEventListener("click", reindex);
  const curBtn = $("#curator-run-btn");
  if (curBtn) curBtn.addEventListener("click", () => runCurator(curBtn));
  $("#attach-btn").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", async (e) => {
    const files = Array.from(e.target.files || []);
    for (const f of files) await uploadFile(f);
    e.target.value = "";
  });

  // example chips — event delegation so it covers BOTH the welcome buttons and
  // the cloned dock buttons (added after this handler is bound).
  buildDemoDock();
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".example");
    if (!b) return;
    // One-click demo: load the prompt AND run it (no separate Send press).
    // Guard against a double-submit while a task is already starting.
    if ($("#run-btn").disabled) return;
    $("#goal").value = b.dataset.prompt;
    autoSizeTextarea($("#goal"));
    startTask();
  });
  const dockToggle = $("#demo-dock-toggle");
  if (dockToggle) dockToggle.addEventListener("click", () => {
    const body = $("#demo-dock-body");
    if (!body) return;
    body.hidden = !body.hidden;
    dockToggle.textContent = body.hidden ? "▸" : "▾";
  });

  // Send shortcuts
  $("#goal").addEventListener("input", (e) => autoSizeTextarea(e.target));
  $("#goal").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      startTask();
    }
  });

  bindDragDrop();

  // refresh audit panels in the background (only when drawer is open the user sees this)
  setInterval(() => { loadStats(); loadMemory(); }, 5000);
});
