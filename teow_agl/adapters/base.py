"""Planner adapter protocol. Mechanical I/O bridge only."""
from __future__ import annotations

from typing import Protocol


class PlannerAdapter(Protocol):
    planner_id: str

    def plan(self, planning_brief: dict, system_prompt: str) -> dict:
        ...
