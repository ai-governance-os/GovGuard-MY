"""High-precision grounding for user-requested output mentions.

A bare noun match is not enough: in ``draft a circular about the
timetable`` the circular is the deliverable and the timetable is only its
subject. This module keeps that distinction consistent across deterministic
source recovery, intent contracts, office-tool selection and verification.
"""
from __future__ import annotations

import re
from typing import Pattern


OUTPUT_REQUEST_CUE = re.compile(
    r"\b(?:add|attach|build|compile|compose|convert|create|design|develop|"
    r"draft|draw|edit|email|export|fill|generate|give\s+me|issue|make|need|"
    r"populate|post|prepare|produce|provide|publish|render|save|send|share|"
    r"submit|supply|turn|update|want|write)\b|"
    r"\b(?:bina|buat|cipta|e-?mel|emel|hasilkan|hantar|karang|kongsi|"
    r"maklumkan|mahu|perlukan|sediakan|siarkan|terbitkan|tulis)\b|"
    r"(?:生成|制作|建立|创建|写|做成|准备|整理成|输出|另存为|请做)",
    re.IGNORECASE,
)

# Only cues between the nearest request verb and the candidate output noun
# suppress the match. Therefore ``prepare a presentation about X`` requests a
# presentation, while ``prepare a roster covering the presentation`` does not.
_TOPIC_OR_CONTENT_CUE = re.compile(
    r"\b(?:about|concerning|covering|describing|discussing|explaining|for|"
    r"including|containing|on(?![-\s]call\b)|regarding|summari[sz]ing)\b|"
    r"\binform(?:ing)?\b[^,;:.!?\n]{0,80}\b(?:about|of|on|regarding)\b|"
    r"\b(?:berkenaan|berkaitan|mengenai|tentang|meliputi|merangkumi|"
    r"termasuk|mengandungi|menerangkan|membincangkan|memaklumkan)\b",
    re.IGNORECASE,
)

_CLAUSE_SPLIT = re.compile(r"[.!?;\n。！？；]+")


def is_requested_output_mention(
    text: str,
    output_pattern: str | Pattern[str],
    *,
    request_pattern: Pattern[str] | None = None,
) -> bool:
    """Return true when ``output_pattern`` is a requested deliverable.

    A match needs a request/action cue in the same clause. If a topic/content
    cue lies between the nearest request cue and the output noun, that noun is
    contextual content rather than a separately requested artifact. A later
    request cue resets the relationship.
    """
    if not text:
        return False
    output_rx = (
        output_pattern
        if isinstance(output_pattern, re.Pattern)
        else re.compile(output_pattern, re.IGNORECASE)
    )
    request_rx = request_pattern or OUTPUT_REQUEST_CUE

    for clause in _CLAUSE_SPLIT.split(str(text)):
        if not clause.strip():
            continue
        request_matches = list(request_rx.finditer(clause))
        if not request_matches:
            continue
        for output_match in output_rx.finditer(clause):
            preceding = [
                match for match in request_matches
                if match.end() <= output_match.start()
            ]
            overlapping = [
                match for match in request_matches
                if output_match.start() <= match.start() < output_match.end()
                and match.end() <= output_match.end()
            ]
            if not preceding and overlapping:
                # Some configured output patterns include the verb itself,
                # e.g. ``generate an image`` or ``draw a picture``.
                return True
            if not preceding:
                continue
            nearest_request = preceding[-1]
            relation_text = clause[nearest_request.end():output_match.start()]
            if _TOPIC_OR_CONTENT_CUE.search(relation_text):
                continue
            return True
    return False
