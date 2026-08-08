from __future__ import annotations

import re

from kcode.memory.models import CompletedTurn, SignalResult

SIGNALS: dict[str, tuple[re.Pattern[str], ...]] = {
    "explicit": (
        re.compile(r"\bremember\b", re.IGNORECASE),
        re.compile(r"\bkeep in mind\b", re.IGNORECASE),
        re.compile(r"记住|请记得|以后要"),
    ),
    "preference": (
        re.compile(r"\bI (?:always |usually )?(?:prefer|like|want)\b", re.IGNORECASE),
        re.compile(r"\bmy preference\b", re.IGNORECASE),
        re.compile(r"我(?:更)?(?:喜欢|偏好|习惯)|我的偏好"),
    ),
    "feedback": (
        re.compile(r"\b(?:don't|do not|never) do that\b", re.IGNORECASE),
        re.compile(r"\bthat's (?:wrong|incorrect)\b", re.IGNORECASE),
        re.compile(r"不是这样|你错了|不要再|以后别|应该改成"),
    ),
    "project": (
        re.compile(r"\bwe (?:use|decided|require)\b", re.IGNORECASE),
        re.compile(r"\bproject (?:uses|requires|convention|rule)\b", re.IGNORECASE),
        re.compile(r"项目(?:使用|规定|约定|必须)|我们决定|架构是"),
    ),
    "reference": (
        re.compile(r"https?://\S+", re.IGNORECASE),
        re.compile(r"\b(?:docs?|documentation|reference)\b", re.IGNORECASE),
        re.compile(r"参考(?:文档|资料)|文档在|链接是"),
    ),
}


class MemorySignalDetector:
    def detect(self, turn: CompletedTurn) -> SignalResult:
        text = f"{turn.user_text}\n{turn.final_text}"
        kinds = tuple(
            name
            for name, patterns in SIGNALS.items()
            if any(pattern.search(text) for pattern in patterns)
        )
        return SignalResult(matched=bool(kinds), kinds=kinds)
