from __future__ import annotations

import re


def glob_regex(
    pattern: str,
    *,
    path: bool = True,
    question_mark: bool = True,
) -> re.Pattern[str]:
    output = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                output.append(".*")
                index += 2
                continue
            output.append("[^/]*" if path else ".*")
        elif character == "?" and question_mark:
            output.append("[^/]" if path else ".")
        else:
            output.append(re.escape(character))
        index += 1
    output.append("$")
    return re.compile("".join(output))


def glob_matches(
    pattern: str,
    value: str,
    *,
    path: bool = True,
    question_mark: bool = True,
) -> bool:
    return glob_regex(pattern, path=path, question_mark=question_mark).fullmatch(value) is not None
