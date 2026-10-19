"""Per-run job log: each entry is committed immediately (the DB is the source
of truth) and then handed to ``on_entry`` for live broadcast."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from insights.core.llm import LLMResult
from insights.db.models import JobLogEntry, JobLogKind

EntryCallback = Callable[[dict[str, Any]], None]


class JobLog:
    def __init__(
        self,
        session: Session,
        run_id: int,
        model: str,
        smart_model: str,
        preview_chars: int = 600,
        on_entry: EntryCallback | None = None,
    ) -> None:
        self._session = session
        self.run_id = run_id
        self._model = model
        self._smart_model = smart_model
        self._preview_chars = preview_chars
        self._on_entry = on_entry

    def info(self, message: str, **meta: object) -> None:
        self._emit(JobLogKind.INFO, message, **meta)

    def step(self, message: str, **meta: object) -> None:
        self._emit(JobLogKind.STEP, message, **meta)

    def error(self, message: str, **meta: object) -> None:
        self._emit(JobLogKind.ERROR, message, **meta)

    def llm_call(self, smart: bool, prompt: str, system: str | None = None, **meta: object) -> None:
        model = self._resolve(smart)
        payload = dict(meta)
        payload.update(
            model=model,
            tier="smart" if smart else "regular",
            prompt_chars=len(prompt),
            system_chars=len(system or ""),
            waiting=True,
            preview=prompt[: self._preview_chars],
        )
        self._emit(JobLogKind.WAIT, f"waiting for {model} - prompt {len(prompt)} chars", **payload)

    def llm_reply(self, smart: bool, result: LLMResult, **meta: object) -> None:
        model = self._resolve(smart)
        payload = dict(meta)
        payload.update(
            model=model,
            tier="smart" if smart else "regular",
            tokens=result.tokens_used,
            elapsed_ms=result.elapsed_ms,
        )
        message = f"<- {model}: {result.tokens_used} tokens in {result.elapsed_ms / 1000:.1f}s"
        self._emit(JobLogKind.LLM, message, **payload)

    def _resolve(self, smart: bool) -> str:
        return self._smart_model if smart else self._model

    def _emit(self, kind: JobLogKind, message: str, **meta: object) -> None:
        entry = JobLogEntry(run_id=self.run_id, kind=kind.value, message=message, meta=dict(meta))
        self._session.add(entry)
        self._session.commit()
        if self._on_entry is not None:
            self._on_entry(entry_payload(entry))


def entry_payload(entry: JobLogEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "kind": entry.kind,
        "message": entry.message,
        "meta": entry.meta or {},
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
