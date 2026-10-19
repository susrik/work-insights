# insights/integrations

Readers for data outside the database. Nothing here writes anywhere.

- `notes.py`: `NotesReader` over the markdown tree: lists notes (skipping
  hidden paths, `AGENTS.md` and `notes.exclude`), dates them (frontmatter,
  filename, mtime), builds the `AGENTS.md` context chain, and finds people
  folders. `note_body` produces the numbered text intake sends, cutting
  rolling `old.md` files to the window. `modified` is naive UTC so it
  compares with `JobRun.started_at`.
- Discord and Google are reserved (config sections only); see the roadmap.

Rules: read-only; never resolve a path outside `root`; no network.
