"""Read-only view of the markdown notes tree.

Conventions the tree uses:

- ``AGENTS.md`` files hold context for their folder; the chain from the root
  down to a note's folder becomes the system context for intake.
- Notes are dated by frontmatter ``date:``, else an ISO date in the filename,
  else mtime. Rolling files (``old.md``) hold ``## YYYY-MM-DD`` sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

CONTEXT_FILENAME = "AGENTS.md"
ROLLING_FILENAMES = frozenset({"old.md"})

_FILENAME_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_FRONTMATTER_DATE = re.compile(r"^date:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_SECTION_DATE = re.compile(r"^##\s+(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*$")


@dataclass(frozen=True)
class NoteFile:
    path: str  # relative posix path
    date: date
    modified: datetime  # naive UTC, comparable with JobRun timestamps


def _parse_date(year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def note_date(rel_path: str, text: str, modified: datetime) -> date:
    match = _FRONTMATTER_DATE.search(text[:500])
    if match:
        return date.fromisoformat(match.group(1))
    name_match = _FILENAME_DATE.search(Path(rel_path).name)
    if name_match:
        parsed = _parse_date(*name_match.groups())
        if parsed is not None:
            return parsed
    return modified.date()


@dataclass
class NotesReader:
    root: Path
    exclude: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()

    def _excluded(self, rel: str) -> bool:
        return any(rel == ex or rel.startswith(ex.rstrip("/") + "/") for ex in self.exclude)

    def iter_notes(self) -> list[NoteFile]:
        """All non-hidden ``.md`` notes, excluding AGENTS.md and excluded paths."""
        notes: list[NoteFile] = []
        if not self.root.is_dir():
            return notes
        for path in sorted(self.root.rglob("*.md")):
            rel = path.relative_to(self.root).as_posix()
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            if path.name == CONTEXT_FILENAME or self._excluded(rel):
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(tzinfo=None)
            text = path.read_text(errors="replace")
            notes.append(NoteFile(path=rel, date=note_date(rel, text, modified), modified=modified))
        return notes

    def changed_since(self, since: datetime | None, window_start: date) -> list[NoteFile]:
        """Notes modified after ``since``; with ``since=None``, notes dated or
        modified inside the window."""
        result = []
        for note in self.iter_notes():
            if since is not None:
                if note.modified > since:
                    result.append(note)
            elif note.date >= window_start or note.modified.date() >= window_start:
                result.append(note)
        return result

    def read(self, rel_path: str) -> str:
        target = (self.root / rel_path).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"path escapes notes root: {rel_path}")
        return target.read_text(errors="replace")

    def context_chain(self, rel_path: str) -> str:
        """Concatenate AGENTS.md files from the root down to the note's folder."""
        parts: list[str] = []
        folder = Path(rel_path).parent
        chain = [Path()] + [Path(*folder.parts[: i + 1]) for i in range(len(folder.parts))]
        for sub in chain:
            candidate = self.root / sub / CONTEXT_FILENAME
            if candidate.is_file():
                text = candidate.read_text(errors="replace").strip()
                if text:
                    origin = sub.as_posix() if sub.parts else "(notes root)"
                    parts.append(f"# Context from: {origin}\n\n{text}")
        return "\n\n---\n\n".join(parts)

    def people_dirs(self, people_dir: str) -> list[str]:
        """Sub-folders of ``people_dir`` (one per team member), minus excluded ones."""
        base = self.root / people_dir
        if not base.is_dir():
            return []
        return sorted(
            f"{people_dir}/{p.name}"
            for p in base.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
            and not self._excluded(f"{people_dir}/{p.name}")
        )


NumberedLines = list[tuple[int, str]]


def numbered_lines(text: str) -> NumberedLines:
    return list(enumerate(text.splitlines(), start=1))


def in_window_lines(lines: NumberedLines, window_start: date) -> NumberedLines:
    """For rolling files with ``## YYYY-MM-DD`` headings keep only in-window
    sections. Files without dated headings are returned unchanged."""
    if not any(_SECTION_DATE.match(line) for _, line in lines):
        return lines
    keep: NumberedLines = []
    current_in_window = False
    for number, line in lines:
        match = _SECTION_DATE.match(line)
        if match:
            parsed = _parse_date(*match.groups())
            current_in_window = parsed is not None and parsed >= window_start
        if current_in_window:
            keep.append((number, line))
    return keep


def format_numbered(lines: NumberedLines) -> str:
    return "\n".join(f"{number}: {line}" for number, line in lines)


def note_body(reader: NotesReader, note: NoteFile, window_start: date, max_chars: int) -> str:
    """The text intake sends for a note: numbered lines, rolling files cut to
    the window, truncated to ``max_chars``. Empty string when nothing is left."""
    lines = numbered_lines(reader.read(note.path))
    if note.path.rsplit("/", 1)[-1] in ROLLING_FILENAMES:
        lines = in_window_lines(lines, window_start)
    if not any(text.strip() for _, text in lines):
        return ""
    body = format_numbered(lines)
    return body[:max_chars]
