from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_INSTRUCTION_BYTES = 32 * 1024
MAX_INCLUDE_DEPTH = 5
_INCLUDE_RE = re.compile(r"^\s*@include\s+<([^>]+)>\s*$")


class _InstructionBudgetExceeded(Exception):
    pass


@dataclass(frozen=True, slots=True)
class InstructionSource:
    level: str
    path: Path
    boundary: Path


@dataclass(frozen=True, slots=True)
class InstructionWarning:
    code: str
    path: Path
    detail: str


@dataclass(frozen=True, slots=True)
class InstructionBundle:
    content: str
    warnings: tuple[InstructionWarning, ...]
    loaded_paths: tuple[Path, ...]
    truncated: bool


class InstructionLoader:
    def __init__(
        self,
        *,
        max_bytes: int = MAX_INSTRUCTION_BYTES,
        max_include_depth: int = MAX_INCLUDE_DEPTH,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("Instruction budget must be positive.")
        if max_include_depth < 0:
            raise ValueError("Include depth cannot be negative.")
        self.max_bytes = max_bytes
        self.max_include_depth = max_include_depth

    def load(self, project_root: Path, user_home: Path | None = None) -> InstructionBundle:
        project = project_root.resolve()
        home = (user_home or Path.home()).resolve()
        sources = (
            InstructionSource("user", home / ".kcode" / "KCODE.md", home / ".kcode"),
            InstructionSource("project", project / "KCODE.md", project),
            InstructionSource("project-local", project / ".kcode" / "KCODE.md", project),
        )
        warnings: list[InstructionWarning] = []
        loaded_paths: list[Path] = []
        sections: list[str] = []
        truncated = False

        prefix = (
            "KCode project instructions follow. When rules conflict, later sources win: "
            "user < project < project-local.\n"
        )
        used = len(prefix.encode("utf-8"))
        for source in sources:
            try:
                source.path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                warnings.append(InstructionWarning("unreadable", source.path, str(exc)))
                continue
            source_loaded_paths: list[Path] = []
            try:
                expanded = self._expand(
                    source.path,
                    source.boundary,
                    depth=0,
                    chain=(),
                    warnings=warnings,
                    loaded_paths=source_loaded_paths,
                )
            except _InstructionBudgetExceeded:
                truncated = True
                continue
            if expanded is None:
                continue
            section = f"\n[source: {source.level} | {source.path}]\n{expanded.rstrip()}\n"
            section_size = len(section.encode("utf-8"))
            if used + section_size > self.max_bytes:
                warnings.append(
                    InstructionWarning(
                        "budget_exceeded",
                        source.path,
                        f"Skipped complete source; {self.max_bytes}-byte budget exhausted.",
                    )
                )
                truncated = True
                continue
            sections.append(section)
            loaded_paths.extend(source_loaded_paths)
            used += section_size

        truncated = truncated or any(
            warning.code in {"budget_exceeded", "source_too_large"} for warning in warnings
        )
        content = prefix + "".join(sections) if sections else ""
        return InstructionBundle(
            content=content,
            warnings=tuple(warnings),
            loaded_paths=tuple(dict.fromkeys(loaded_paths)),
            truncated=truncated,
        )

    def _expand(
        self,
        path: Path,
        boundary: Path,
        *,
        depth: int,
        chain: tuple[Path, ...],
        warnings: list[InstructionWarning],
        loaded_paths: list[Path],
    ) -> str | None:
        try:
            resolved_boundary = boundary.resolve()
            resolved = path.resolve()
        except OSError as exc:
            warnings.append(InstructionWarning("unreadable", path, str(exc)))
            return None
        if resolved != resolved_boundary and resolved_boundary not in resolved.parents:
            warnings.append(
                InstructionWarning("boundary_escape", path, "Resolved path leaves its source root.")
            )
            return None
        if depth > self.max_include_depth:
            warnings.append(
                InstructionWarning(
                    "include_depth",
                    path,
                    f"Include depth exceeds {self.max_include_depth}.",
                )
            )
            return None
        if resolved in chain:
            warnings.append(InstructionWarning("include_cycle", path, "Include cycle detected."))
            return None

        text = self._read_text(resolved, warnings)
        if text is None:
            return None
        loaded_paths.append(resolved)
        next_chain = (*chain, resolved)
        output: list[str] = []
        output_bytes = 0

        def append(piece: str) -> None:
            nonlocal output_bytes
            output_bytes += len(piece.encode("utf-8"))
            if output_bytes > self.max_bytes:
                warnings.append(
                    InstructionWarning(
                        "source_too_large",
                        resolved,
                        "Expanded source exceeds total instruction budget.",
                    )
                )
                raise _InstructionBudgetExceeded
            output.append(piece)

        for line in text.splitlines(keepends=True):
            match = _INCLUDE_RE.fullmatch(line.rstrip("\r\n"))
            if match is None:
                append(line)
                continue
            relative = Path(match.group(1))
            if relative.is_absolute():
                warnings.append(
                    InstructionWarning(
                        "absolute_include", resolved, f"Absolute include rejected: {relative}"
                    )
                )
                continue
            included = self._expand(
                resolved.parent / relative,
                resolved_boundary,
                depth=depth + 1,
                chain=next_chain,
                warnings=warnings,
                loaded_paths=loaded_paths,
            )
            if included is not None:
                append(included)
                if included and not included.endswith("\n") and line.endswith(("\n", "\r")):
                    append("\n")
        return "".join(output)

    def _read_text(
        self,
        path: Path,
        warnings: list[InstructionWarning],
    ) -> str | None:
        try:
            with path.open("rb") as handle:
                payload = handle.read(self.max_bytes + 1)
        except OSError as exc:
            warnings.append(InstructionWarning("unreadable", path, str(exc)))
            return None
        if len(payload) > self.max_bytes:
            warnings.append(
                InstructionWarning(
                    "source_too_large",
                    path,
                    "File exceeds total instruction budget.",
                )
            )
            raise _InstructionBudgetExceeded
        if b"\x00" in payload:
            warnings.append(InstructionWarning("binary", path, "Binary instruction file skipped."))
            return None
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            warnings.append(InstructionWarning("invalid_utf8", path, str(exc)))
            return None
