"""Jira issue keys in free text. v1 only detects and links keys; the read-only
fetch of summary/status is on the roadmap."""

from __future__ import annotations

import re

JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def find_jira_keys(text: str | None) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    for key in JIRA_KEY.findall(text):
        if key not in seen:
            seen.append(key)
    return seen


def normalize_keys(keys: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in keys or []:
        key = raw.strip().upper()
        if JIRA_KEY.fullmatch(key) and key not in out:
            out.append(key)
    return out


def project_of(key: str) -> str:
    return key.rsplit("-", 1)[0]
