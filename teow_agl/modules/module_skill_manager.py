"""
Skill Manager — procedural memory store.

After a non-trivial successful task, the agent can save a `SKILL_<id>.md`
describing **how to do this kind of task**. Future tasks whose goal is
*similar* (by BM25 over skill name+description) have the skill auto-
injected into the planning brief as a hint.

How this differs from the existing learning surfaces:

  * `plan_cache.jsonl` (EXECUTE_DIRECT): caches concrete *action
    templates* by exact category. Identity match only. Skips the LLM
    entirely.
  * `subject_confidence.jsonl`: tracks per-category success rate.
    Numerical only.
  * `USER.md` / `MEMORY.md`: free-form notes about the user /
    environment. Not procedural.
  * **`SKILL_*.md` (this module): markdown narratives "when user wants
    X, the approach is Y". Pulled by SIMILARITY, not identity. Still
    requires the LLM to interpret.**

Hermes has the same SKILL.md pattern. The differentiator here is
**governance**: skill creation goes through a config-driven policy
filter (forbidden patterns, length caps, task-quality gate), so a
malicious LLM can't silently plant a poisoned skill that steers all
future tasks.

Storage layout:

  state/skills/
    index.jsonl           # one line per skill metadata record
    SKILL_<id>.md         # the narrative procedure body

Index records are append-only audit history; the *current* state of
each skill is determined by the LATEST record bearing its `skill_id`.
This matches the `policies/plan_cache.py` discipline so we can replay
state on startup just by scanning the JSONL.

Each skill record:

  {
    "skill_id":      "skill_<hex>",
    "name":          "short slug",
    "description":   "1-2 sentence summary used for retrieval",
    "created_at":    ISO-8601,
    "updated_at":    ISO-8601,
    "task_id":       "task_<hex>   # the task that created it",
    "status":        "active" | "archived",
    "usage_count":   int,           # incremented on each retrieval hit
    "char_length":   int,
    "tags":          [str, ...]     # optional, free-form for filtering
  }
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..rag.bm25 import BM25Index, tokenize


SKILL_ID_RE = re.compile(r"^skill_[a-z0-9]{8,16}$")

# Per-skill embedding file lives alongside SKILL_<id>.md so archive /
# inspect operations don't need to touch a side database. Lazy-loaded:
# `find_relevant` only reads the ones it needs.
_EMBEDDING_SUFFIX = ".embedding.json"


class SkillManager:
    """File-backed procedural memory with BM25 retrieval + governance.

    All write operations apply the constraints from
    `configs/skill_constraints.json`. Read operations are unrestricted
    (skills are agent state; user can inspect anything).
    """

    module_id = "SKILL"

    # Phase 2 L4.8 — the 4-state skill lifecycle (roadmap §5.1):
    #   active     → fully eligible for retrieval (weight 1.0)
    #   stale      → not used recently; still retrievable at reduced
    #                weight (default 0.5) so it can prove itself again
    #   superseded → a newer same-shape skill exists; excluded from
    #                retrieval (weight 0.0) but kept for audit
    #   archived   → soft-deleted; excluded from retrieval entirely
    _VALID_STATUSES = ("active", "stale", "superseded", "archived")

    def __init__(
        self,
        directory: str | Path,
        *,
        constraints: dict | None = None,
    ) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.jsonl"
        self.constraints = constraints or {}
        self._lock = threading.Lock()
        # Compile forbidden-pattern regexes once
        self._forbidden: list[re.Pattern] = []
        for pat in (self.constraints.get("forbidden_patterns") or {})\
                .get("patterns", []) or []:
            try:
                self._forbidden.append(re.compile(pat))
            except re.error:
                continue
        # Phase 2 — list of (event, details) tuples produced during the
        # most recent write op. Runtime can flush these into the trace
        # via `pop_audit_events()`. Kept lightweight to avoid coupling
        # SkillManager to the runtime's trace pipeline.
        self._pending_audit: list[tuple[str, dict]] = []

    # ------------------------------------------------------------------
    # Phase 2 — embedding persistence (one .embedding.json per skill,
    # alongside SKILL_<id>.md). Failure isolated: missing / unreadable
    # files just return None so callers fall back to BM25.
    # ------------------------------------------------------------------
    def _embedding_path(self, skill_id: str) -> Path:
        return self.dir / f"SKILL_{skill_id}{_EMBEDDING_SUFFIX}"

    def load_embedding(self, skill_id: str) -> list[float] | None:
        """Return the persisted embedding vector for a skill, or None
        if the file is missing / malformed / empty.

        Defensive — never raises. Used by find_relevant to lazy-load
        only the skills it ranks.
        """
        if not SKILL_ID_RE.match(skill_id or ""):
            return None
        path = self._embedding_path(skill_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        vec = data.get("vector") if isinstance(data, dict) else None
        if not isinstance(vec, list) or not vec:
            return None
        try:
            return [float(x) for x in vec]
        except (TypeError, ValueError):
            return None

    def _save_embedding(
        self, skill_id: str, vector: list[float], model: str = "",
    ) -> bool:
        """Persist the vector. Returns True on success, False on any
        write error (caller decides whether that's fatal)."""
        path = self._embedding_path(skill_id)
        payload = {
            "skill_id": skill_id,
            "model": model,
            "vector": list(vector),
            "dim": len(vector),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            return False
        return True

    def pop_audit_events(self) -> list[tuple[str, dict]]:
        """Return + clear the audit events recorded during the last
        write op. Runtime calls this right after create_skill to surface
        events like `skill_embedding_failed` into the trace pipeline."""
        events = list(self._pending_audit)
        self._pending_audit.clear()
        return events

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------
    def list_skills(
        self,
        *,
        include_archived: bool = False,
        statuses: Iterable[str] | None = None,
    ) -> list[dict]:
        """Return current state of each skill (latest record wins).

        Filtering precedence:
          * `statuses` (Phase 2 L4.8): when given, return ONLY skills
            whose status is in this set. Overrides `include_archived`.
            Use e.g. ``statuses=("active", "stale")`` for retrieval, or
            ``statuses=SkillManager._VALID_STATUSES`` for "everything".
          * `include_archived` (legacy): False → active-only (default);
            True → all statuses. Kept for backwards-compatibility with
            every existing caller.
        """
        latest: dict[str, dict] = {}
        for rec in self._replay():
            sid = rec.get("skill_id")
            if sid:
                latest[sid] = rec
        out = list(latest.values())
        if statuses is not None:
            wanted = set(statuses)
            out = [r for r in out if r.get("status", "active") in wanted]
        elif not include_archived:
            out = [r for r in out if r.get("status") == "active"]
        # Newest first
        out.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return out

    def read_skill(self, skill_id: str) -> str:
        """Return the SKILL_<id>.md body. Empty string if missing."""
        if not SKILL_ID_RE.match(skill_id or ""):
            return ""
        path = self.dir / f"SKILL_{skill_id}.md"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def find_active_for(
        self, *, category: str, plan_shape: str | None = None,
    ) -> dict | None:
        """Phase 1A — locate an existing ACTIVE skill for a (category,
        plan_shape) pair. Used by Module 109B Skill Distiller to skip
        proposing a duplicate when a same-shape skill is already in use.

        Returns the skill record (dict) or None if no match.

        - `category` matches the skill's `source_category` field.
        - `plan_shape` is optional; when None, returns the first ACTIVE
          skill for the category. When given, also matches `source_shape`.
        """
        if not category:
            return None
        for rec in self.list_skills(include_archived=False):
            if rec.get("source_category") != category:
                continue
            if plan_shape is not None:
                if rec.get("source_shape") != plan_shape:
                    continue
            return rec
        return None

    def find_relevant(
        self, goal: str, *, top_k: int = 3,
        min_score: float | None = None,
    ) -> list[dict]:
        """Cosine-similarity retrieval over embedded ACTIVE skills, with
        BM25 fallback per-skill when embedding is missing or when the
        embedding lane is entirely unavailable.

        Returns `[{skill_id, name, description, tags, score, body,
        char_length, usage_count, rank_method}]` sorted by descending
        score. `rank_method` is either `"cosine"` or `"bm25"` so callers
        / tests can see which lane scored each hit.

        Decision tree:
          1. Empty goal / no skills → []
          2. If embedding provider available AND query embeds OK:
             a. compute cosine for each skill that HAS an embedding file
             b. take top-k by cosine above min_score
             c. if fewer than top_k results, fill from BM25 over the
                REMAINING skills (those without embeddings or scored low)
          3. Otherwise: pure BM25 over all skills (legacy Phase-1A path)
        """
        if not goal:
            return []
        # Phase 2 L4.8 — retrieval pool is ACTIVE + STALE skills, each
        # scored then multiplied by its status weight. SUPERSEDED and
        # ARCHIVED skills carry weight 0.0 so they are filtered out here
        # (never embedded, never BM25'd, never injected).
        skills = [
            s for s in self.list_skills(statuses=("active", "stale"))
            if self._retrieval_weight(s.get("status", "active")) > 0.0
        ]
        if not skills:
            return []
        status_by_id = {
            s["skill_id"]: s.get("status", "active") for s in skills
        }
        if min_score is None:
            min_score = float(
                (self.constraints.get("retrieval") or {})
                .get("min_score_for_injection", 0.0)
            )

        # Try cosine route first. embedding_provider_available is a
        # cheap env-var check (no network). The query embed call IS a
        # network round-trip but we only pay it once per find_relevant.
        cosine_results: list[tuple[str, float]] = []
        embedded_ids: set[str] = set()
        try:
            from teow_agl.util.embeddings import (
                embed_one, embedding_provider_available, cosine_similarity,
            )
            if embedding_provider_available():
                query_vec = embed_one(goal)
                if query_vec:
                    for s in skills:
                        sid = s["skill_id"]
                        vec = self.load_embedding(sid)
                        if vec is None:
                            continue
                        # Phase 2 L4.8 — multiply by status weight so a
                        # STALE skill ranks below an equally-similar
                        # ACTIVE one (and can drop below min_score).
                        score = cosine_similarity(query_vec, vec) * \
                            self._retrieval_weight(status_by_id.get(sid, "active"))
                        if score >= min_score:
                            cosine_results.append((sid, score))
                            embedded_ids.add(sid)
                    cosine_results.sort(key=lambda x: x[1], reverse=True)
                    cosine_results = cosine_results[:top_k]
        except Exception:
            # Embedding lane crashed — fall through to BM25 like nothing
            # happened. failure-isolation contract.
            cosine_results = []
            embedded_ids = set()

        # Fill remaining top-k slots from BM25 over the skills NOT
        # already returned via cosine (so we don't double-count).
        cosine_ids = {sid for sid, _ in cosine_results}
        slots_left = top_k - len(cosine_results)
        bm25_results: list[tuple[str, float]] = []
        if slots_left > 0:
            remaining_skills = [s for s in skills
                                if s["skill_id"] not in cosine_ids]
            if remaining_skills:
                idx = BM25Index()
                for s in remaining_skills:
                    haystack = f"{s.get('name','')} {s.get('description','')} "
                    haystack += " ".join(s.get("tags") or [])
                    idx.add(s["skill_id"], tokenize(haystack))
                idx.finalize()
                # Score ALL remaining candidates, apply the lifecycle status
                # weight, THEN sort by the weighted score and truncate. The
                # status weight (active=1.0 > stale=0.5) is what makes an
                # ACTIVE skill rank strictly above an equal-but-stale one with
                # an identical description. Without this re-sort the BM25 lane
                # ties on the raw score and staleness never breaks the tie —
                # the deterministic failure the spec's Task 5 calls out. (The
                # cosine lane already sorts by weighted score; this matches it.)
                for sid, score in idx.score_query(
                    tokenize(goal), top_k=len(remaining_skills),
                ):
                    weighted = score * self._retrieval_weight(
                        status_by_id.get(sid, "active"))
                    if weighted >= min_score:
                        bm25_results.append((sid, weighted))
                bm25_results.sort(key=lambda x: x[1], reverse=True)
                bm25_results = bm25_results[:slots_left]

        # Stitch the final list together: cosine hits first (higher
        # semantic match), BM25 fillers after.
        by_id = {s["skill_id"]: s for s in skills}
        out: list[dict] = []

        def _emit(sid: str, score: float, method: str) -> None:
            meta = by_id.get(sid)
            if meta is None:
                return
            body = self.read_skill(sid)
            out.append({
                "skill_id": sid,
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "tags": meta.get("tags") or [],
                "score": round(float(score), 3),
                "body": body,
                "char_length": meta.get("char_length", len(body)),
                "usage_count": int(meta.get("usage_count", 0)) + 1,
                "success_count": int(meta.get("success_count", 0)),
                "rank_method": method,
            })
            # Persist the usage bump (best-effort).
            try:
                self._bump_usage(meta)
            except Exception:
                pass

        for sid, score in cosine_results:
            _emit(sid, score, "cosine")
        for sid, score in bm25_results:
            _emit(sid, score, "bm25")
        return out

    def find_cross_context(
        self, goal: str, *, target_tool: str, top_k: int = 1,
        min_score: float | None = None,
    ) -> list[dict]:
        """P2 (option B) — a deliberately separate, LOWER-threshold
        retrieval lane for CROSS-TOOL transfer.

        Rationale: the normal `find_relevant` threshold
        (`min_score_for_injection`) is kept conservative on purpose —
        in a governed system a missed skill is harmless (the task just
        runs without a head-start) while a wrongly-injected one can
        mislead. So instead of lowering that global knob to satisfy
        cross-medium cases (e.g. a learned *docx* procedure reused for a
        *pptx* task scoring ~0.35), this lane fires ONLY as a fallback,
        ONLY for an explicit target tool, and returns ONLY skills whose
        stored tool DIFFERS from that target. The caller must then route
        the hit through adaptation + the verifier's strict mode, which
        decide whether the transfer is actually safe — the threshold is
        not trusted to.

        Returns the same dict shape as `find_relevant`, each hit tagged
        `cross_context=True` and carrying its `source_shape`. Empty when
        no genuinely cross-tool candidate clears `cross_context_min_score`.

        NB: like `find_relevant`, retrieved hits have their usage_count
        bumped; over-retrieval is bounded (top_k + 2 wide net) to keep
        that side-effect small.
        """
        if not goal or not target_tool:
            return []
        if min_score is None:
            min_score = float(
                (self.constraints.get("retrieval") or {})
                .get("cross_context_min_score", 0.30)
            )
        tt = target_tool.strip().lower()
        # Map skill_id -> stored tool (source_shape) so we can keep only
        # genuinely cross-tool candidates. Same pool find_relevant uses.
        shape_by_id = {
            s["skill_id"]: str(s.get("source_shape") or "").strip().lower()
            for s in self.list_skills(statuses=("active", "stale"))
        }
        wide = self.find_relevant(
            goal, top_k=max(top_k + 2, 3), min_score=min_score,
        )
        out: list[dict] = []
        for hit in wide:
            stored = shape_by_id.get(hit["skill_id"], "")
            # Only a DIFFERENT, known stored tool qualifies as cross-context.
            if stored and stored != tt:
                h = dict(hit)
                h["cross_context"] = True
                h["source_shape"] = stored
                out.append(h)
            if len(out) >= top_k:
                break
        return out

    @staticmethod
    def _normalize_procedure(proc: str) -> str:
        """P6 — render a stringified list of steps as clean numbered
        markdown. The Distiller's draft model sometimes emits the
        procedure as a Python/JSON list literal
        (`["Identify the topic", 'Draft each section']`) which, stored
        verbatim, makes an ugly SKILL_<id>.md. Best-effort and pure: if
        `proc` doesn't look like a list, or parsing fails, the original
        string is returned unchanged."""
        s = (proc or "").strip()
        if not (s.startswith("[") and s.endswith("]")):
            return s
        items = None
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                items = parsed
        except Exception:
            try:
                import ast
                parsed = ast.literal_eval(s)
                if isinstance(parsed, (list, tuple)):
                    items = list(parsed)
            except Exception:
                items = None
        if not items:
            return s
        lines = []
        for i, item in enumerate(items, 1):
            txt = str(item).strip()
            if txt:
                lines.append(f"{i}. {txt}")
        return "\n".join(lines) if lines else s

    # ------------------------------------------------------------------
    # Write API — all writes go through governance
    # ------------------------------------------------------------------
    def create_skill(
        self,
        *,
        name: str,
        description: str,
        procedure: str,
        task_id: str = "",
        task_quality: dict | None = None,
        tags: Iterable[str] | None = None,
        source_category: str = "",
        source_shape: str = "",
        principle: str = "",
        parameters: dict | None = None,
        abstraction_model: str = "",
    ) -> dict:
        """Create a new SKILL_<id>.md after policy checks.

        Args:
            source_category / source_shape: Phase-1A additions used by
              `find_active_for()` to prevent the Skill Distiller from
              proposing a duplicate skill for the same (category, shape)
              pair. Both default to "" so existing callers (skill_tool,
              tests) keep working without changes.

            principle / parameters: Phase-2 additions from the Distiller's
              abstraction pass. `principle` is a one-sentence "why this
              works" with no tool/language specifics; `parameters` is a
              JSON dict listing the per-instance vars (tool, language,
              sections, ...). Both default empty — legacy skills work fine.
            abstraction_model: free-form provenance string for the
              skill record (e.g. `"openai:gpt-4o-mini"`,
              `"skipped:no_key"`). Useful for analytics / debugging
              "why does this old skill have no principle?".

        Returns:
            {"ok": True, "skill_id": ..., "char_length": ...}
            {"ok": False, "error": "...", ...details}
        """
        # ---- policy gates ---------------------------------------------
        name = (name or "").strip()
        description = (description or "").strip()
        # P6 — the Distiller's draft sometimes arrives as a stringified
        # list of steps (e.g. `["Identify the topic", 'Draft sections']`).
        # Normalise that into clean numbered markdown BEFORE the length /
        # forbidden-pattern gates run, so the gates measure the real
        # stored text and the persisted SKILL_<id>.md reads cleanly.
        procedure = self._normalize_procedure((procedure or "").strip())
        if not name:
            return {"ok": False, "error": "empty_name"}
        if not description:
            return {"ok": False, "error": "empty_description"}
        if not procedure:
            return {"ok": False, "error": "empty_procedure"}

        limits = self.constraints.get("creation_limits", {}) or {}
        max_chars = int(limits.get("max_chars_per_skill", 2000))
        min_chars = int(limits.get("min_chars_per_skill", 60))
        max_total = int(limits.get("max_total_skills", 200))

        if len(procedure) < min_chars:
            return {"ok": False, "error": f"procedure_too_short:min={min_chars}"}
        if len(procedure) > max_chars:
            return {"ok": False, "error": f"procedure_too_long:max={max_chars}"}

        # Quality gate based on the task that's calling us
        quality_gate = self.constraints.get("min_task_quality", {}) or {}
        if task_quality:
            if quality_gate.get("require_blue_or_green_route", True):
                route = (task_quality.get("final_route") or "").upper()
                if route not in ("BLUE", "GREEN"):
                    return {
                        "ok": False,
                        "error": f"task_quality_route_excluded:{route}",
                    }
            if quality_gate.get("skip_if_verification_failed", True):
                if task_quality.get("verification_failed"):
                    return {"ok": False,
                            "error": "task_quality_verification_failed"}
            min_execs = int(quality_gate.get("min_executions", 0) or 0)
            if min_execs and task_quality.get("execution_success_count", 0) < min_execs:
                return {"ok": False,
                        "error": f"task_quality_too_few_executions:min={min_execs}"}

        # Forbidden-pattern scan on EVERY field — name AND body
        for field_name, content in (("name", name),
                                    ("description", description),
                                    ("procedure", procedure)):
            hit = self._scan_forbidden(content)
            if hit is not None:
                return {"ok": False,
                        "error": f"blocked_by_safety:{field_name}:{hit}"}

        # ---- total-skills cap -----------------------------------------
        current = self.list_skills(include_archived=False)
        if len(current) >= max_total:
            return {
                "ok": False,
                "error": f"too_many_active_skills:max={max_total}",
                "current_count": len(current),
            }

        # Phase 2 — abstraction fields. We canonicalise the input here
        # so downstream code doesn't have to worry about None / weird
        # types: principle becomes a stripped str, parameters becomes a
        # plain dict (or empty dict).
        principle_clean = (principle or "").strip()
        params_clean: dict = (parameters or {}) if isinstance(parameters,
                                                              dict) else {}

        # ---- write ----------------------------------------------------
        skill_id = "skill_" + uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        body_path = self.dir / f"SKILL_{skill_id}.md"
        # Phase 2: body now carries optional Principle + Parameters
        # sections when present. Legacy skills (no abstraction) keep
        # the same single-section layout for backwards-compatibility.
        sections: list[str] = [
            f"# {name}",
            f"_{description}_",
            (f"<!-- skill_id: {skill_id} | task_id: {task_id} | "
             f"created: {now} -->"),
        ]
        if principle_clean:
            sections.append("## Principle\n" + principle_clean)
        if params_clean:
            sections.append(
                "## Parameters\n```json\n"
                + json.dumps(params_clean, ensure_ascii=False, indent=2)
                + "\n```"
            )
        sections.append("## Procedure\n" + procedure)
        body_with_header = "\n\n".join(sections) + "\n"

        record = {
            "skill_id": skill_id,
            "name": name,
            "description": description,
            "created_at": now,
            "updated_at": now,
            "task_id": task_id,
            "status": "active",
            "usage_count": 0,
            # Phase 2 (L4.7) — SUCCESS counter, distinct from usage_count.
            # usage_count bumps on every retrieval hit; success_count only
            # bumps when a task that USED this skill PASSED verification
            # (failure-isolation contract, roadmap §3.7). The Curator (§5 /
            # L4.8) compares the two to find skills that get retrieved a lot
            # but rarely lead to a verified-good outcome.
            "success_count": 0,
            "char_length": len(procedure),
            "tags": sorted(set(t.strip().lower() for t in (tags or [])
                               if t and t.strip())),
            # Phase 1A — source_category / source_shape persisted so
            # find_active_for() can match. Empty strings for legacy
            # skills created before this field existed.
            "source_category": (source_category or "").strip(),
            "source_shape":    (source_shape or "").strip(),
            # Phase 2 — abstraction provenance. `principle` is stored
            # in the record (small, makes list-skills cheap) but the
            # full parameters dict is in the body markdown.
            "principle":         principle_clean,
            "has_principle":     bool(principle_clean),
            "parameters_count":  len(params_clean),
            "abstraction_model": (abstraction_model or "").strip(),
        }
        # Reset the audit-event queue for this write so callers only
        # see events from THIS create_skill call.
        self._pending_audit.clear()
        with self._lock:
            try:
                body_path.write_text(body_with_header, encoding="utf-8")
            except Exception as exc:
                return {"ok": False, "error": f"write_failed:{exc}"}
            self._append_index(record)

        # ---- Phase 2: persist embedding (failure-isolated) ----------
        # We embed `name + description + tags` because find_relevant's
        # query is the user's goal — short and descriptive — so we want
        # the skill's RETRIEVAL TEXT (not the full procedure) to be the
        # comparison surface. If the embedding lane is unavailable or
        # fails we just skip silently: the skill is still created and
        # find_relevant will fall back to BM25 for it.
        embedding_persisted = False
        try:
            from teow_agl.util.embeddings import (
                embed_one, embedding_provider_available,
            )
            from teow_agl.adapters.openai_provider import (
                _resolve_embed_model,
            )
            if embedding_provider_available():
                # Phase 2: when a principle is present, embed it instead
                # of the description. Principles are tool/language-
                # agnostic so they give cleaner cosine matches for
                # cross-context retrieval ("write Q3 pptx" matches a
                # skill whose principle was distilled from "write Q3
                # docx", even though name/description differ).
                if principle_clean:
                    retrieval_text = " ".join([
                        name, principle_clean,
                        " ".join(record["tags"] or []),
                    ]).strip()
                else:
                    retrieval_text = " ".join([
                        name, description,
                        " ".join(record["tags"] or []),
                    ]).strip()
                vec = embed_one(retrieval_text)
                if vec:
                    if self._save_embedding(skill_id, vec,
                                            model=_resolve_embed_model()):
                        embedding_persisted = True
                        self._pending_audit.append((
                            "skill_embedding_persisted",
                            {"skill_id": skill_id, "dim": len(vec)},
                        ))
                    else:
                        self._pending_audit.append((
                            "skill_embedding_write_failed",
                            {"skill_id": skill_id, "reason": "write_error"},
                        ))
                else:
                    self._pending_audit.append((
                        "skill_embedding_failed",
                        {"skill_id": skill_id, "reason": "provider_returned_none"},
                    ))
            else:
                self._pending_audit.append((
                    "skill_embedding_skipped",
                    {"skill_id": skill_id, "reason": "provider_unavailable"},
                ))
        except Exception as exc:
            # Any unexpected error during the embedding pass is silently
            # caught — the skill creation itself already succeeded and
            # we will fall back to BM25 for this skill.
            self._pending_audit.append((
                "skill_embedding_failed",
                {"skill_id": skill_id, "reason": f"exception:{exc}"},
            ))

        return {
            "ok": True,
            "skill_id": skill_id,
            "char_length": len(procedure),
            "path": str(body_path),
            "embedding_persisted": embedding_persisted,
        }

    def archive_skill(self, skill_id: str) -> dict:
        """Soft-archive a skill (never auto-deleted)."""
        if not SKILL_ID_RE.match(skill_id or ""):
            return {"ok": False, "error": "invalid_skill_id"}
        current = {s["skill_id"]: s for s in self.list_skills(include_archived=True)}
        meta = current.get(skill_id)
        if meta is None:
            return {"ok": False, "error": "skill_not_found"}
        if meta.get("status") == "archived":
            return {"ok": True, "already_archived": True}
        record = dict(meta)
        record["status"] = "archived"
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._append_index(record)
        return {"ok": True, "skill_id": skill_id}

    # ------------------------------------------------------------------
    # Phase 2 L4.8 — 4-state lifecycle transitions
    #
    # Legal transitions (roadmap §5.1):
    #   active     → stale       (auto, time-based; lifecycle_sweep)
    #   active     → superseded  (proposed; newer same-shape skill exists)
    #   stale      → superseded  (proposed)
    #   stale      → archived    (proposed; long-unused)
    #   any        → archived    (manual; archive_skill)
    #   stale/superseded/archived → active (manual restore)
    #
    # All transitions are append-only index records (latest wins), so the
    # full history is preserved for audit.
    # ------------------------------------------------------------------
    def _retrieval_weight(self, status: str) -> float:
        """Multiplier applied to a skill's retrieval score based on its
        lifecycle status. ACTIVE is always 1.0; STALE / SUPERSEDED are
        config-tunable (defaults: 0.5 / 0.0)."""
        if status == "active":
            return 1.0
        lifecycle = self.constraints.get("lifecycle") or {}
        if status == "stale":
            return float(lifecycle.get("stale_retrieval_weight", 0.5))
        if status == "superseded":
            return float(lifecycle.get("superseded_retrieval_weight", 0.0))
        # archived / unknown
        return 0.0

    def _transition(
        self,
        skill_id: str,
        new_status: str,
        *,
        allowed_from: tuple[str, ...] | None,
        extra: dict | None = None,
    ) -> dict:
        if not SKILL_ID_RE.match(skill_id or ""):
            return {"ok": False, "error": "invalid_skill_id"}
        current = {s["skill_id"]: s
                   for s in self.list_skills(statuses=self._VALID_STATUSES)}
        meta = current.get(skill_id)
        if meta is None:
            return {"ok": False, "error": "skill_not_found"}
        cur_status = meta.get("status", "active")
        if cur_status == new_status:
            return {"ok": True, "skill_id": skill_id,
                    "status": new_status, "noop": True}
        if allowed_from is not None and cur_status not in allowed_from:
            return {"ok": False,
                    "error": f"illegal_transition:{cur_status}->{new_status}"}
        record = dict(meta)
        record["status"] = new_status
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        if extra:
            record.update(extra)
        with self._lock:
            self._append_index(record)
        return {"ok": True, "skill_id": skill_id, "status": new_status}

    def mark_stale(self, skill_id: str) -> dict:
        """ACTIVE → STALE. Auto-applied by lifecycle_sweep when a skill
        has gone unused past `active_to_stale_days_unused`."""
        return self._transition(skill_id, "stale", allowed_from=("active",))

    def mark_superseded(self, skill_id: str, *, superseded_by: str = "") -> dict:
        """ACTIVE|STALE → SUPERSEDED. Records which newer skill replaced
        it. Excluded from retrieval but kept for audit."""
        return self._transition(
            skill_id, "superseded", allowed_from=("active", "stale"),
            extra={"superseded_by": (superseded_by or "").strip()},
        )

    def restore_skill(self, skill_id: str) -> dict:
        """STALE|SUPERSEDED|ARCHIVED → ACTIVE. Manual un-deprecation."""
        return self._transition(
            skill_id, "active",
            allowed_from=("stale", "superseded", "archived"),
        )

    @staticmethod
    def _days_since(iso_ts: str | None, now: datetime) -> float | None:
        """Whole+fractional days between `iso_ts` and `now`. None if the
        timestamp is missing / unparseable. Naive timestamps are treated
        as UTC."""
        if not iso_ts:
            return None
        try:
            dt = datetime.fromisoformat(iso_ts)
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() / 86400.0

    def _detect_supersede_candidates(self) -> list[dict]:
        """Find ACTIVE skills that share a (source_category, source_shape)
        with a NEWER ACTIVE skill. Returns proposal candidates
        ``[{skill_id, superseded_by}]`` — does NOT apply the transition
        (supersede goes through the human gate, roadmap §5.1).

        Skills without BOTH a category and a shape are skipped — we can't
        confidently call two shapeless skills "the same".
        """
        actives = self.list_skills(statuses=("active",))
        by_shape: dict[tuple[str, str], list[dict]] = {}
        for s in actives:
            cat = (s.get("source_category") or "").strip()
            shape = (s.get("source_shape") or "").strip()
            if not cat or not shape:
                continue
            by_shape.setdefault((cat, shape), []).append(s)
        out: list[dict] = []
        for group in by_shape.values():
            if len(group) < 2:
                continue
            # Newest by created_at wins; the rest are superseded by it.
            ranked = sorted(group, key=lambda r: r.get("created_at", ""),
                            reverse=True)
            winner = ranked[0]
            for loser in ranked[1:]:
                out.append({"skill_id": loser["skill_id"],
                            "superseded_by": winner["skill_id"]})
        return out

    def lifecycle_sweep(self, *, now: datetime | None = None) -> dict:
        """Phase 2 L4.8 — periodic lifecycle maintenance (roadmap §5.1).

        Auto-applies the only low-risk, reversible transition
        (ACTIVE → STALE on long-unused skills) and RETURNS proposals for
        the rest (STALE → ARCHIVED, ACTIVE → SUPERSEDED), which the
        runtime routes through the 105 human gate.

        `updated_at` doubles as "last touched" — it bumps on every
        retrieval hit (`_bump_usage`) and success (`bump_usage_success`),
        so "days since updated_at" is effectively "days unused".

        Returns::

            {
              "marked_stale":  [skill_id, ...],          # applied
              "proposals":     [{type, skill_id, ...}],  # for human gate
            }

        proposal types: ``supersede_skill`` (carries superseded_by) and
        ``archive_skill`` (STALE→ARCHIVED).
        """
        now = now or datetime.now(timezone.utc)
        lifecycle = self.constraints.get("lifecycle") or {}
        stale_days = int(lifecycle.get("active_to_stale_days_unused", 90))
        archive_days = int(lifecycle.get("stale_to_archived_days_unused", 180))
        supersede_enabled = bool(
            lifecycle.get("supersede_on_new_skill_same_shape", True))

        out: dict = {"marked_stale": [], "proposals": []}

        # --- ACTIVE → STALE (auto) -------------------------------------
        for s in self.list_skills(statuses=("active",)):
            age = self._days_since(s.get("updated_at"), now)
            if age is not None and age >= stale_days:
                res = self.mark_stale(s["skill_id"])
                if res.get("ok") and not res.get("noop"):
                    out["marked_stale"].append(s["skill_id"])

        # --- ACTIVE → SUPERSEDED (proposed) ----------------------------
        if supersede_enabled:
            for cand in self._detect_supersede_candidates():
                out["proposals"].append({
                    "type": "supersede_skill",
                    "skill_id": cand["skill_id"],
                    "superseded_by": cand["superseded_by"],
                    "reason": "newer_same_shape_skill",
                    "auto_nominated": True,
                })

        # --- STALE → ARCHIVED (proposed) -------------------------------
        for s in self.list_skills(statuses=("stale",)):
            age = self._days_since(s.get("updated_at"), now)
            if age is not None and age >= archive_days:
                out["proposals"].append({
                    "type": "archive_skill",
                    "skill_id": s["skill_id"],
                    "reason": f"stale_unused_{archive_days}d",
                    "auto_nominated": True,
                })

        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _replay(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        out: list[dict] = []
        try:
            with self.index_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return out
        return out

    def _append_index(self, record: dict) -> None:
        with self.index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _bump_usage(self, meta: dict) -> None:
        new_record = dict(meta)
        new_record["usage_count"] = int(meta.get("usage_count", 0)) + 1
        new_record["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._append_index(new_record)

    def bump_usage_success(self, skill_id: str) -> dict:
        """Increment the per-skill SUCCESS counter.

        Phase 2 (L4.7). Distinct from `usage_count` (retrieval hits):
        `success_count` only bumps when a task that USED this skill
        PASSED verification. The runtime calls this from
        `_record_task_outcome` ONLY on the success branch — the
        failure-isolation contract (roadmap §3.7) means a failed task
        (including one whose skill adaptation produced garbage) never
        credits the skill. Append-only; latest record wins on replay.

        Returns `{ok, skill_id, success_count}` or `{ok: False, error}`.
        Never raises for a missing/invalid id — the caller is in a
        best-effort outcome-recording path.
        """
        if not SKILL_ID_RE.match(skill_id or ""):
            return {"ok": False, "error": "invalid_skill_id"}
        current = {s["skill_id"]: s
                   for s in self.list_skills(include_archived=True)}
        meta = current.get(skill_id)
        if meta is None:
            return {"ok": False, "error": "skill_not_found"}
        new_record = dict(meta)
        new_record["success_count"] = int(meta.get("success_count", 0)) + 1
        new_record["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._append_index(new_record)
        return {"ok": True, "skill_id": skill_id,
                "success_count": new_record["success_count"]}

    def _scan_forbidden(self, content: str) -> str | None:
        if not content:
            return None
        for rx in self._forbidden:
            if rx.search(content):
                return f"forbidden_pattern:{rx.pattern[:60]}"
        return None
