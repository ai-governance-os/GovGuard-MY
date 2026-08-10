import pytest

from teow_agl.modules.module_deliverable_mentions import (
    is_requested_output_mention,
)


@pytest.mark.parametrize(
    ("text", "pattern", "expected"),
    [
        ("Draft the examination timetable.", r"\btimetable\b", True),
        (
            "Draft a circular informing parents of the examination timetable.",
            r"\btimetable\b",
            False,
        ),
        ("Prepare a supplier payment schedule.", r"\bschedule\b", True),
        (
            "Prepare a procurement memo including the supplier payment schedule.",
            r"\bschedule\b",
            False,
        ),
        ("Prepare a presentation about school safety.", r"\bpresentation\b", True),
        (
            "Prepare a duty roster covering assembly and prize presentation.",
            r"\bpresentation\b",
            False,
        ),
        (
            "Prepare a duty roster and create slides for the prize presentation.",
            r"\bslides\b",
            True,
        ),
        ("Sediakan jadual peperiksaan.", r"\bjadual\s+peperiksaan\b", True),
        (
            "Sediakan notis kepada ibu bapa mengenai jadual peperiksaan.",
            r"\bjadual\s+peperiksaan\b",
            False,
        ),
        (
            "Sediakan minit mesyuarat yang membincangkan jadual peperiksaan.",
            r"\bjadual\s+peperiksaan\b",
            False,
        ),
    ],
)
def test_requested_output_mention_distinguishes_deliverable_from_topic(
    text: str, pattern: str, expected: bool,
) -> None:
    assert is_requested_output_mention(text, pattern) is expected
