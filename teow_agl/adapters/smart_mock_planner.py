"""Deterministic-but-realistic offline planner for the UI demo."""
from __future__ import annotations

import re
import uuid
from pathlib import Path


_OFFICE_EXT_TO_TOOL = {
    ".docx": "docx", ".doc": "docx",
    ".pptx": "pptx", ".ppt": "pptx",
    ".xlsx": "xlsx", ".xls": "xlsx",
}


class SmartMockPlanner:
    planner_id = "smart_mock_planner"

    def __init__(self, *, default_outputs_dir: str = "./outputs") -> None:
        self.default_outputs_dir = default_outputs_dir

    def plan(self, planning_brief: dict, system_prompt: str) -> dict:
        category = planning_brief.get("task_category", "unknown")
        intent = planning_brief.get("user_intent", "")
        task_id = planning_brief.get("task_id", "unknown")

        if category == "office_doc_generation":
            return self._office_plan(task_id, intent, planning_brief)
        if category == "image_generation":
            return self._image_plan(task_id, intent, planning_brief)
        if category == "report_generation":
            return self._report_plan(task_id, intent, planning_brief)
        if category == "gui_automation":
            return self._gui_plan(task_id, intent, planning_brief)
        if category in ("file_delete", "desktop_org"):
            return self._refusal(task_id, "context_sensitive_overrefusal",
                                 f"{category} best handled by 102R staged template")
        if category in ("file_write",):
            return self._fs_write_plan(task_id, intent, planning_brief)
        if category == "parent_message_draft_edit":
            return self._parent_message_edit_plan(task_id, intent, planning_brief)
        return self._refusal(task_id, "empty_plan", f"smart_mock has no template for category={category}")

    def _office_plan(self, task_id: str, intent: str, brief: dict) -> dict:
        target_path, tool = self._extract_office_target(intent)
        title, body = self._summarize(intent)
        if tool == "docx":
            metadata = {"title": title, "body": body}
        elif tool == "pptx":
            metadata = {
                "title": title, "subtitle": "Auto-drafted by SmartMockPlanner",
                "slides": [{"title": title, "bullets": body.split("\n")[:5]}],
            }
        else:
            metadata = {"sheets": {"Summary": [["Topic", title], ["Notes", body]]}}
        return self._wrap(task_id, brief, [
            {
                "action_id": f"act_{uuid.uuid4().hex[:8]}",
                "tool": tool, "operation": "save_under_outputs",
                "target": str(target_path), "purpose": f"draft {tool} document",
                "expected_effect": f"{tool} file saved",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": metadata,
            }
        ])

    def _report_plan(self, task_id: str, intent: str, brief: dict) -> dict:
        target = Path(self.default_outputs_dir) / "report.md"
        title, body = self._summarize(intent)
        return self._wrap(task_id, brief, [
            {
                "action_id": f"act_{uuid.uuid4().hex[:8]}",
                "tool": "report", "operation": "draft_report",
                "target": str(target), "purpose": title or "draft report",
                "expected_effect": "produce report markdown",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"topic": title, "body": body},
            },
            {
                "action_id": f"act_{uuid.uuid4().hex[:8]}",
                "tool": "fs", "operation": "save_under_outputs",
                "target": str(target), "purpose": "save report",
                "expected_effect": "file written",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"content": f"# {title}\n\n{body}\n"},
            },
        ])

    def _parent_message_edit_plan(self, task_id: str, intent: str, brief: dict) -> dict:
        """A safe internal edit to a parent-message DRAFT (BLUE): add the
        requested follow-up logistics, keep a warm tone, send nothing. Mock mode
        emits a deterministic, faithful revised draft (no LLM, no apology, no
        placeholder) so the demo's BLUE probe always shows a real updated notice.
        Governance is unchanged — editing a draft is BLUE; only releasing/sending
        is GREEN."""
        body = (
            "【已更新草稿 / Updated draft — Parent Notice for Mei Xin】  "
            "Safe internal edit (BLUE) — the requested follow-up details were added; "
            "nothing is sent.\n\n"
            "Subject: Congratulations on Mei Xin's National-Level Achievement and New Record\n\n"
            "Dear Mr. Lee,\n\n"
            "Warm greetings from Demo Primary School. We are very proud that Mei Xin won the "
            "Gold Medal in the Long Jump U12 Girls event at the 2026 National Primary Schools "
            "Athletics Championship and set a new national primary schools record.\n\n"
            "We would also like to share a preliminary follow-up arrangement. The Malaysia "
            "Schools Invitational Athletics Meet in Singapore is expected to take place about "
            "one month after this national championship. To support Mei Xin's preparation, a "
            "five-day centralised training session will be held one week before the Singapore "
            "event at Johor Bahru Sports Arena. The school will share the confirmed schedule, "
            "consent form and travel-related details once they are finalised.\n\n"
            "Congratulations once again to Mei Xin and your family.\n\n"
            "With appreciation,\nTeacher-in-charge of Athletics Team, Demo Primary School\n\n"
            "(Safe internal draft edit — nothing is sent in demo mode. The warm wording "
            "follows this parent's recorded school-message style; no household income, "
            "occupation, address, phone, PIBG status or donation data was used to set tone "
            "or priority. Contact data, if used, is limited to authorised delivery routing only.)"
        )
        return self._wrap(task_id, brief, [{
            "action_id": f"act_{uuid.uuid4().hex[:8]}",
            "tool": "chat", "operation": "answer",
            "target": "", "purpose": "revise Mei Xin's parent-message draft (safe internal edit)",
            "expected_effect": "updated parent-notice draft (not sent)",
            "reversibility": "high", "uncertainty": "low",
            "risk_factors": [], "requires_governance": True,
            "metadata": {"body": body, "synthesis_skip": True},
        }])

    def _gui_plan(self, task_id: str, intent: str, brief: dict) -> dict:
        low = intent.lower()
        # screenshot intent: BLUE (read-only)
        if "screenshot" in low or "capture screen" in low:
            return self._wrap(task_id, brief, [{
                "action_id": f"act_{uuid.uuid4().hex[:8]}",
                "tool": "gui", "operation": "screenshot",
                "target": "", "purpose": "capture current screen",
                "expected_effect": "saves PNG under outputs/_screenshots/",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True, "metadata": {},
            }])
        # type intent
        m_type = re.search(r"type ['\"]([^'\"]{1,200})['\"]", intent, flags=re.IGNORECASE)
        if m_type:
            text = m_type.group(1)
            return self._wrap(task_id, brief, [
                {
                    "action_id": f"act_{uuid.uuid4().hex[:8]}",
                    "tool": "gui", "operation": "screenshot",
                    "target": "", "purpose": "capture state before typing",
                    "expected_effect": "screenshot for review",
                    "reversibility": "high", "uncertainty": "low",
                    "risk_factors": [], "requires_governance": True, "metadata": {},
                },
                {
                    "action_id": f"act_{uuid.uuid4().hex[:8]}",
                    "tool": "gui", "operation": "keyboard_type",
                    "target": "", "purpose": f"type {len(text)} chars into focused window",
                    "expected_effect": "characters typed into focused window",
                    "reversibility": "low", "uncertainty": "medium",
                    "risk_factors": [], "requires_governance": True,
                    "metadata": {"text": text},
                },
            ])
        # hotkey intent
        m_hk = re.search(r"press\s+([A-Za-z0-9]+(?:\s*\+\s*[A-Za-z0-9]+){0,3})", intent, flags=re.IGNORECASE)
        if m_hk:
            combo = m_hk.group(1).replace(" ", "")
            return self._wrap(task_id, brief, [{
                "action_id": f"act_{uuid.uuid4().hex[:8]}",
                "tool": "gui", "operation": "keyboard_hotkey",
                "target": "", "purpose": f"press hotkey {combo}",
                "expected_effect": "hotkey sent to focused window",
                "reversibility": "low", "uncertainty": "medium",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"combo": combo},
            }])
        # click intent: extract coords if present
        m_xy = re.search(r"\(?\s*(\d{1,4})\s*[,\s]\s*(\d{1,4})\s*\)?", intent)
        if "click" in low and m_xy:
            x, y = int(m_xy.group(1)), int(m_xy.group(2))
            return self._wrap(task_id, brief, [
                {
                    "action_id": f"act_{uuid.uuid4().hex[:8]}",
                    "tool": "gui", "operation": "screenshot",
                    "target": "", "purpose": "capture state before clicking",
                    "expected_effect": "screenshot for review",
                    "reversibility": "high", "uncertainty": "low",
                    "risk_factors": [], "requires_governance": True, "metadata": {},
                },
                {
                    "action_id": f"act_{uuid.uuid4().hex[:8]}",
                    "tool": "gui", "operation": "mouse_click",
                    "target": "", "purpose": f"click at ({x},{y})",
                    "expected_effect": "left click at requested coords",
                    "reversibility": "low", "uncertainty": "medium",
                    "risk_factors": [], "requires_governance": True,
                    "metadata": {"x": x, "y": y, "button": "left"},
                },
            ])
        # generic gui request -> defer to 102R
        return self._refusal(task_id, "context_sensitive_overrefusal",
                             "gui_automation needs specific operation; routing through 102R")

    def _image_plan(self, task_id: str, intent: str, brief: dict) -> dict:
        # Extract a prompt from the intent: drop the imperative prefix.
        prompt = re.sub(
            r"^(generate|draw|make|create|render|illustrate|please)\s+(an?\s+)?"
            r"(image|picture|illustration|drawing)(\s+of)?\s*",
            "", intent.strip(), flags=re.IGNORECASE,
        ).strip() or intent.strip()
        return self._wrap(task_id, brief, [{
            "action_id": f"act_{uuid.uuid4().hex[:8]}",
            "tool": "image_gen", "operation": "generate_image",
            "target": "",
            "purpose": f"image: {prompt[:120]}",
            "expected_effect": "PNG saved under outputs/_images/",
            "reversibility": "high", "uncertainty": "low",
            "risk_factors": [], "requires_governance": True,
            "metadata": {"prompt": prompt, "size": "1024x1024"},
        }])

    def _fs_write_plan(self, task_id: str, intent: str, brief: dict) -> dict:
        target = Path(self.default_outputs_dir) / "note.txt"
        return self._wrap(task_id, brief, [
            {
                "action_id": f"act_{uuid.uuid4().hex[:8]}",
                "tool": "fs", "operation": "save_under_outputs",
                "target": str(target), "purpose": "save user note",
                "expected_effect": "file written",
                "reversibility": "high", "uncertainty": "low",
                "risk_factors": [], "requires_governance": True,
                "metadata": {"content": intent},
            }
        ])

    def _extract_office_target(self, intent: str) -> tuple[Path, str]:
        match = re.search(r"([A-Za-z0-9_./\\-]+\.(?:docx|pptx|xlsx))", intent, flags=re.IGNORECASE)
        if match:
            raw = match.group(1)
            ext = "." + raw.rsplit(".", 1)[1].lower()
            return Path(self.default_outputs_dir) / Path(raw).name, _OFFICE_EXT_TO_TOOL[ext]
        low = intent.lower()
        if "ppt" in low or "slide" in low or "presentation" in low:
            return Path(self.default_outputs_dir) / "deck.pptx", "pptx"
        if "excel" in low or "xls" in low or "spreadsheet" in low or "workbook" in low:
            return Path(self.default_outputs_dir) / "data.xlsx", "xlsx"
        return Path(self.default_outputs_dir) / "doc.docx", "docx"

    def _summarize(self, intent: str) -> tuple[str, str]:
        title = intent.strip().split("\n", 1)[0][:80]
        body = intent.strip()
        if not body:
            body = "(empty user goal)"
        return title or "Document", body

    def _wrap(self, task_id: str, brief: dict, actions: list[dict]) -> dict:
        return {
            "plan_id": f"plan_{uuid.uuid4().hex[:12]}",
            "task_id": task_id, "planner_id": self.planner_id,
            "planning_mode": brief.get("planning_mode", "direct"),
            "used_refusal_recovery": False, "actions": actions,
            "notes": ["generated by SmartMockPlanner (offline, rule-based)"],
        }

    def _refusal(self, task_id: str, refusal_type: str, message: str) -> dict:
        return {
            "refusal_id": f"refusal_{uuid.uuid4().hex[:12]}",
            "task_id": task_id, "planner_id": self.planner_id,
            "refusal_type": refusal_type, "message": message,
            "raw_output_hash": "",
            "recovery_allowed": refusal_type != "universal_hard_safety_refusal",
        }
