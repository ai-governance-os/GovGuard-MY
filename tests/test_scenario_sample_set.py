"""Phase B4 — canonical sample prompts per professional scenario.

Single source of truth for "what a typical user says" → "which category
should the classifier pick" → "which planner path the runtime takes".
Each sample is a realistic phrasing that should classify cleanly. New
phrasings should be added here BEFORE adding keywords to
intake_classifier.json — that way regressions surface as failed tests
instead of silent mis-routes.

Per the pipeline diagnosis report §11 Phase C, ~10 samples per scenario.
"""
from __future__ import annotations

import json

import pytest

from teow_agl.modules.module_101a_pre_governance import PreGovernanceModule


def _m() -> PreGovernanceModule:
    cls = json.load(open(
        "configs/intake_classifier.json", encoding="utf-8"))
    return PreGovernanceModule(
        intake_classifier=cls, hard_safety_cfg={}, learned_policy={})


# ===========================================================================
# Sample set 1 — identity / capability (Phase A2)
# ===========================================================================
_IDENTITY_SAMPLES = [
    "你是谁?",
    "你是什么?",
    "你叫什么?",
    "你能做什么",
    "你可以做什么",
    "介绍一下你自己",
    "自我介绍",
    "who are you?",
    "what can you do",
    "introduce yourself",
    "what is teow-agl",
]

@pytest.mark.parametrize("goal", _IDENTITY_SAMPLES)
def test_sample_identity_classifies_correctly(goal):
    assert _m()._classify(goal) == "identity_capability"


# ===========================================================================
# Sample set 2 — §6 office document (B1)
# ===========================================================================
_OFFICE_SAMPLES = [
    # Chinese — Word / docx
    "写一份关于AI治理的500字报告",
    "写一份关于Q3销售的总结",
    "做一份2026年趋势文档",
    # Chinese — PPT
    "做一份关于AI的演示文稿",
    "做一份Q3总结幻灯片",
    # Chinese — Excel
    "做一份预算电子表格",
    "做一份销售数据表格",
    # English — Word.  ("write a 500-word report" → report_generation
    # because 'report' matches report's keyword set first; that's
    # intentional. Office samples use phrases that DON'T contain bare
    # 'report' so they route to office.)
    "draft a doc summarizing Q3 sales",
    "make a doc on AI safety",
    # English — PPT
    "make a slide deck about Q3 sales",
    "create a deck about AI safety",
    "build a pitch deck for our startup",
    # English — Excel
    "make a spreadsheet of Q3 sales numbers",
    "create an excel workbook of monthly metrics",
]

@pytest.mark.parametrize("goal", _OFFICE_SAMPLES)
def test_sample_office_doc_classifies_correctly(goal):
    assert _m()._classify(goal) == "office_doc_generation"


# ===========================================================================
# Sample set 3 — §7 research report (B2)
# ===========================================================================
_RESEARCH_SAMPLES = [
    # Chinese
    "搜索并总结最新AI新闻",
    "搜索后总结一下行业动态",
    "查资料后写一份关于Tesla的报告",
    "帮我调研一下电动车市场",
    "找几个来源,总结成500字",
    "附引用的报告关于量子计算",
    # English
    "research and write a 500-word report on AI safety",
    "research and summarize Tesla's earnings",
    "search and summarize the latest news on quantum computing",
    "find sources for AI governance",
    "look up and summarize recent SEC filings",
]

@pytest.mark.parametrize("goal", _RESEARCH_SAMPLES)
def test_sample_research_classifies_correctly(goal):
    assert _m()._classify(goal) == "research_report"


# ===========================================================================
# Sample set 4 — §8 patent / legal draft (B3)
# ===========================================================================
_PATENT_LEGAL_SAMPLES = [
    # Chinese — patent
    "帮我写专利草案",
    "写一份专利草案关于一种新的训练方法",
    "起草专利交底书",
    "帮我整理 claims",
    "撰写专利权利要求",
    # Chinese — contracts / NDA. (Classifier matches by substring; the
    # full phrase must appear contiguously. "起草一份软件许可合同" does
    # NOT match because "软件许可" interrupts the "起草一份合同" pattern;
    # users get around this by saying "帮我起草一份合同..." instead.)
    "起草一份保密协议",
    "帮我起草一份合同关于软件许可",
    "起草协议给两家公司",
    "帮我审阅合同",
    # English — patent
    "draft a patent disclosure for my ML system",
    "draft a patent application for the quantum sensor",
    "patent claims for a new sorting algorithm",
    # English — contracts / NDA
    "draft an NDA between Alice and Bob",
    "draft a contract for software licensing",
    "review the contract attached",
    "draft a non-disclosure agreement",
]

@pytest.mark.parametrize("goal", _PATENT_LEGAL_SAMPLES)
def test_sample_patent_legal_classifies_correctly(goal):
    assert _m()._classify(goal) == "patent_legal_draft"


# ===========================================================================
# Sample set 5 — image generation (existing)
# ===========================================================================
_IMAGE_SAMPLES = [
    "画一张鲤鱼图",
    "生成一张落日的图片",
    "生成图像 of a koi pond",
    "画一张关于Q3的图",
    "draw a picture of a cat",
    "generate an image of a sunset",
    "create an image of a koi fish",
    "render an image of mountains",
    "illustrate the concept of recursion",
]

@pytest.mark.parametrize("goal", _IMAGE_SAMPLES)
def test_sample_image_classifies_correctly(goal):
    assert _m()._classify(goal) == "image_generation"


# ===========================================================================
# Sample set 6 — must NOT misfire (boundary cases)
# Each (goal, expected_category) pair documents a tricky case where a
# scenario keyword might collide with another category's keyword. If a
# future keyword change breaks one of these, this test fails loudly.
# ===========================================================================
_BOUNDARY_CASES = [
    # Philosophy / general knowledge — must stay `unknown`
    ("什么是人生的意义", "unknown"),
    ("explain quantum tunneling", "unknown"),
    ("how does patent law work", "unknown"),
    ("what is photosynthesis", "unknown"),
    # User asks whether agent can control their computer — this is a
    # capability boundary question (Q3/Q4), NOT identity. Before the
    # capability_boundary category existed it landed in unknown.
    ("你可以去动我的电脑吗", "capability_boundary"),
    # Office task that happens to mention legal — office wins
    ("写一份关于法律的报告", "office_doc_generation"),
    # Bare verb without object — too vague, stays unknown
    ("research is interesting", "unknown"),
    ("legal is hard", "unknown"),
    # Freshness query without research phrase — unknown so the web
    # heuristic decides (which will search because of "最新")
    ("比特币最新价格", "unknown"),
    # Greeting alone — caught by greeting list at runtime, classifier
    # says unknown (which is fine; runtime catches it).
    ("嗨", "unknown"),
    ("你好", "unknown"),
]

@pytest.mark.parametrize("goal,expected", _BOUNDARY_CASES)
def test_boundary_does_not_misclassify(goal, expected):
    assert _m()._classify(goal) == expected
