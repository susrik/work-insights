# docs

Documentation follows [Diátaxis](https://diataxis.fr): four folders, four
purposes. Plain Markdown, readable on GitHub without a build step.

| Folder | Answers | Tone |
|--------|---------|------|
| `tutorials/` | "Teach me": a guided first run, guaranteed to work | Steps, no choices, no theory |
| `how-to/` | "How do I ...": a task the reader already understands | Numbered or short steps; assume the basics |
| `reference/` | "What is ...": config keys, concepts, API, CLI | Tables and definitions; complete and dry |
| `explanation/` | "Why ...": design, requirements, roadmap | Prose; context and trade-offs |

Rules:

- Put a page in exactly one quadrant. If it mixes, split it.
- Link between pages with relative paths.
- `reference/concepts.md` owns the vocabulary; use its words everywhere.
- `explanation/requirements.md` is the traceability table of the user's
  original wishes. When a roadmap item ships, update its row.
- `explanation/roadmap.md` is the ordered backlog. Add to it rather than
  to code comments or issues when deferring work.
- Keep each page short enough to read in one sitting; prefer a new page
  over a long one.
- Update docs in the same change as the code they describe.
