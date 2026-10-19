from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from insights.integrations.notes import (
    NoteFile,
    NotesReader,
    in_window_lines,
    note_body,
    note_date,
    numbered_lines,
)


def make_tree(root: Path) -> None:
    (root / "AGENTS.md").write_text("Root context.")
    (root / "team/people/robert").mkdir(parents=True)
    (root / "team/people/former/old").mkdir(parents=True)
    (root / "team/AGENTS.md").write_text("Team context.")
    (root / "team/people/robert/2026-01-05.md").write_text("- Robert to send the plan\n")
    (root / "team/people/robert/old.md").write_text(
        "## 2020-01-01\n- ancient\n## 2099-12-31\n- future\n"
    )
    (root / "team/people/former/old/note.md").write_text("ignored")
    (root / "overviews").mkdir()
    (root / "overviews/weekly.md").write_text("ignored")
    (root / ".hidden").mkdir()
    (root / ".hidden/x.md").write_text("ignored")
    (root / "front.md").write_text("---\ndate: 2025-12-24\n---\nhello\n")
    (root / "empty.md").write_text("\n\n")


def test_note_date_sources() -> None:
    modified = datetime(2026, 2, 2, 12, 0)
    assert note_date("a/b.md", "---\ndate: 2026-01-01\n---", modified) == date(2026, 1, 1)
    assert note_date("a/2026-1-9-meeting.md", "", modified) == date(2026, 1, 9)
    assert note_date("a/2026-13-99.md", "", modified) == date(2026, 2, 2)
    assert note_date("a/plain.md", "", modified) == date(2026, 2, 2)


def test_reader_lists_and_excludes(tmp_path: Path) -> None:
    make_tree(tmp_path)
    reader = NotesReader(tmp_path, exclude=("team/people/former", "overviews/"))
    paths = [n.path for n in reader.iter_notes()]
    assert paths == [
        "empty.md",
        "front.md",
        "team/people/robert/2026-01-05.md",
        "team/people/robert/old.md",
    ]
    assert reader.people_dirs("team/people") == ["team/people/robert"]
    assert reader.people_dirs("nope") == []
    assert NotesReader(tmp_path / "missing").iter_notes() == []


def test_reader_context_chain_and_read(tmp_path: Path) -> None:
    make_tree(tmp_path)
    reader = NotesReader(tmp_path)
    chain = reader.context_chain("team/people/robert/2026-01-05.md")
    assert "# Context from: (notes root)\n\nRoot context." in chain
    assert "# Context from: team\n\nTeam context." in chain
    assert reader.context_chain("front.md") == "# Context from: (notes root)\n\nRoot context."
    assert reader.read("front.md").startswith("---")
    with pytest.raises(ValueError):
        reader.read("../outside.md")


def test_changed_since(tmp_path: Path) -> None:
    make_tree(tmp_path)
    reader = NotesReader(tmp_path)
    old = tmp_path / "front.md"
    stamp = (datetime.now() - timedelta(days=400)).timestamp()
    os.utime(old, (stamp, stamp))
    window = date.today() - timedelta(days=30)
    in_window = {n.path for n in reader.changed_since(None, window)}
    assert "front.md" not in in_window
    assert "team/people/robert/old.md" in in_window
    recent = {
        n.path
        for n in reader.changed_since(
            datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1), window
        )
    }
    assert "front.md" not in recent
    assert "empty.md" in recent


def test_window_lines_and_body(tmp_path: Path) -> None:
    make_tree(tmp_path)
    reader = NotesReader(tmp_path)
    plain = numbered_lines("a\nb")
    assert in_window_lines(plain, date(2026, 1, 1)) == plain
    rolling = NoteFile("team/people/robert/old.md", date(2026, 1, 1), datetime.now())
    body = note_body(reader, rolling, date(2026, 1, 1), 1000)
    assert body == "3: ## 2099-12-31\n4: - future"
    assert note_body(reader, rolling, date(2100, 1, 1), 1000) == ""
    empty = NoteFile("empty.md", date(2026, 1, 1), datetime.now())
    assert note_body(reader, empty, date(2026, 1, 1), 1000) == ""
    front = NoteFile("front.md", date(2026, 1, 1), datetime.now())
    assert note_body(reader, front, date(2026, 1, 1), 6) == "1: ---"
