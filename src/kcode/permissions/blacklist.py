from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

DEVICE_PATTERN = re.compile(
    r"^/dev/(?:sd[a-z]\d*|vd[a-z]\d*|xvd[a-z]\d*|nvme\d+n\d+(?:p\d+)?|disk\d+|rdisk\d+)$",
    re.IGNORECASE,
)
FORK_BOMB_PATTERN = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")
WINDOWS_FORMAT_PATTERN = re.compile(
    r"(?:^|[;&|]\s*)format(?:\.com)?\s+[a-z]:[\\/]?(?:\s|$)", re.IGNORECASE
)
WINDOWS_DELETE_ROOT_PATTERN = re.compile(
    r"(?:^|[;&|]\s*)(?:del|erase|rd|rmdir)\s+"
    r"(?=[^\r\n;&|]*(?:/s|-s))(?=[^\r\n;&|]*(?:/q|-q))"
    r"[^\r\n;&|]*\s[a-z]:[\\/]?\*?(?:\s|$)",
    re.IGNORECASE,
)
CONTROL_TOKEN_PATTERN = re.compile(r"^(?:&&|\|\||;|\||&)$")


def _segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=os.name != "nt", punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if CONTROL_TOKEN_PATTERN.fullmatch(token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def _strip_prefixes(tokens: list[str]) -> list[str]:
    remaining = list(tokens)
    while remaining and Path(remaining[0]).name.lower() in {"sudo", "command", "env"}:
        prefix = Path(remaining.pop(0)).name.lower()
        if remaining and remaining[0] == "--":
            remaining.pop(0)
        if prefix == "sudo":
            while remaining and remaining[0] in {"-n", "-E", "-H", "-k", "-S"}:
                remaining.pop(0)
        if prefix == "env":
            while remaining and (remaining[0].startswith("-") or "=" in remaining[0]):
                remaining.pop(0)
    return remaining


def _dangerous_segment(tokens: list[str]) -> str | None:
    tokens = _strip_prefixes(tokens)
    if not tokens:
        return None
    executable = Path(tokens[0]).name.lower()
    arguments = tokens[1:]
    lowered = [argument.lower() for argument in arguments]

    if executable in {"sh", "bash", "zsh", "dash", "ksh"} and "-c" in arguments:
        command_index = arguments.index("-c") + 1
        if command_index < len(arguments):
            return dangerous_command_reason(arguments[command_index])

    if executable == "rm":
        short_flags = "".join(
            argument[1:]
            for argument in lowered
            if argument.startswith("-") and not argument.startswith("--")
        )
        recursive = "r" in short_flags or "--recursive" in lowered
        forced = "f" in short_flags or "--force" in lowered
        targets = [argument for argument in arguments if not argument.startswith("-")]
        dangerous_targets = {"/", "/*", "~", "~/", "$HOME", "${HOME}", "$HOME/*", "${HOME}/*"}
        if recursive and forced and any(target in dangerous_targets for target in targets):
            return "recursive forced deletion of a root or home directory"

    if (executable.startswith("mkfs") or executable == "mkswap") and any(
        DEVICE_PATTERN.fullmatch(argument) for argument in arguments
    ):
        return "filesystem formatting of a block device"

    if executable == "dd" and any(
        argument.lower().startswith("of=") and DEVICE_PATTERN.fullmatch(argument[3:])
        for argument in arguments
    ):
        return "direct write to a block device"

    for index, argument in enumerate(arguments[:-1]):
        if argument in {">", ">>"} and DEVICE_PATTERN.fullmatch(arguments[index + 1]):
            return "redirection to a block device"
    if executable in {">", ">>"} and arguments and DEVICE_PATTERN.fullmatch(arguments[0]):
        return "redirection to a block device"

    if executable in {"format", "format.com"} and any(
        re.fullmatch(r"[a-z]:[\\/]?", argument, re.IGNORECASE) for argument in arguments
    ):
        return "formatting a drive root"

    if executable in {"del", "erase", "rd", "rmdir"}:
        flags = {argument.lower() for argument in arguments if argument.startswith(("/", "-"))}
        roots = [
            argument
            for argument in arguments
            if re.fullmatch(r"[a-z]:[\\/]?\*?", argument, re.IGNORECASE)
        ]
        if roots and ({"/s", "/q"} <= flags or {"-s", "-q"} <= flags):
            return "recursive forced deletion of a drive root"

    if executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        joined = " ".join(lowered)
        if "remove-item" in joined and "-recurse" in lowered and "-force" in lowered:
            if re.search(r"[a-z]:[\\/]?(?:\*|\\\*)?(?:\s|$)", joined, re.IGNORECASE):
                return "recursive forced deletion of a drive root"
    return None


def dangerous_command_reason(command: str) -> str | None:
    if FORK_BOMB_PATTERN.search(command):
        return "fork bomb pattern"
    if WINDOWS_FORMAT_PATTERN.search(command):
        return "formatting a drive root"
    if WINDOWS_DELETE_ROOT_PATTERN.search(command):
        return "recursive forced deletion of a drive root"
    for segment in _segments(command):
        reason = _dangerous_segment(segment)
        if reason is not None:
            return reason
    return None
