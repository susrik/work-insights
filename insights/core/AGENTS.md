# insights/core

Service layer. Functions take a `Session` plus plain arguments, return ORM
objects or plain data, and `flush()` but never `commit()`; the caller
(endpoint, job, CLI) commits.

- `threads.py`: create/update, tree (`set_parent`, `descendant_ids`,
  `ancestors`), merge, related links, labels, Jira keys.
- `mentions.py`: `upsert_mention` (fingerprint dedupe), `place_mention`,
  confirm / set aside, filters, `review_groups` for the Review page.
- `intake.py`: one LLM pass per note (`extract_and_place`), then
  `apply_result` creates provisional mentions/threads. Jira-key and label
  hints are applied deterministically around the model. Prompt text is
  `SYSTEM_PROMPT`; the reply schema is `IntakeResult`.
- `changes.py`: `record` every mutation; `undo` one, `revert_run` all for
  a job. Coerces JSON-stored values back to enums/dates.
- `people.py`, `labels.py`: CRUD, `resolve_person` (folder first, then
  name/alias), `labels_for_hints`.
- `jira_keys.py`: key regex, normalisation.
- `llm.py`: `LLMClient` protocol, `OpenAILLM`, `FakeLLM`, `complete_json`.

Rules:

- No FastAPI, Jinja or click imports here.
- Set `thread.parent` / `mention.thread` through the relationship, not the
  `_id` column, so loaded collections stay consistent.
- Raise `ValueError` for not-found and invalid input; the server maps it
  to 400/404.
- When you add a field that the user can change, record it in `changes`.
