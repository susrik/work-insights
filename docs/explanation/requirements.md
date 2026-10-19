# Requirements and where they are covered

work-insights replaces an earlier tool (ewo) that never became usable: three
months of notes produced 2–3,000 extracted items, and triaging them into
tasks was more work than the planning they were meant to support. This page
lists every concrete wish from the original brief and says where the design
covers it, so nothing is lost as the project evolves.

"Covered" means the design and the code path exist in v1. "Roadmap" points
to [roadmap.md](roadmap.md). Column three gives the place to look.

| # | Wish | Status | Where |
|---|------|--------|-------|
| 1 | Same stack as ewo: Python, FastAPI, htmx, Tailwind, SQLAlchemy, Alembic, click | Covered | `pyproject.toml`; [architecture.md](architecture.md) |
| 2 | Help me make plans for myself and my team, short and near term | Covered | Today page (focus ranking), Threads tree; planning horizons on roadmap |
| 3 | Track what has been agreed | Covered | Thread kind `agreement`; Person page lists open agreements |
| 4 | Better Jira linking; keys in notes become a sorting signal; not a mirror of Jira | Covered | Key detection `core/jira_keys.py`; `jira_links` table; label hints by Jira project; intake uses keys as a placement signal |
| 5 | Data is private; no accounts; the app talks to nobody | Covered | No auth, no outbound calls except the configured LLM; config git-ignored |
| 6 | Make my life easier, not harder: no fountain of items to map by hand | Covered | Provisional-by-default intake: AI places every mention immediately; review is confirm/move/set-aside |
| 7 | Richer than ewo tasks: things that actually need doing, with hierarchy | Covered | Threads with single-parent tree and kinds `work`, `agreement`, `goal` |
| 8 | Labels plus something like "topics"; propose alternatives | Covered | Labels only, with path/Jira hints so they apply themselves; see [concepts.md](../reference/concepts.md) |
| 9 | Know my priorities easily, with fine control | Covered | Priority field, Today ranking; every field editable |
| 10 | Refactoring and triage must be simple; split a too-generic bucket easily | Covered | Move/merge/reparent in the Threads page; `insights_scan` proposes splits (phase 5) |
| 11 | AI help is optional and always validated; no big suggestions page but full editing still possible | Covered | Insights shown contextually on the thread with Apply/Dismiss; full edit forms remain |
| 12 | Every change reversible; triage can be redone | Covered | `changes` table; undo per change, revert per job run |
| 13 | New name for "nugget"; keep a link back to the note file (file only, no line numbers) | Covered | **Mention** with `path`; `notes.repo_url_template` renders the GitHub link |
| 14 | Notes can change after import | Roadmap | Fingerprint dedupe handles re-scans; change tracking is roadmap item 9 |
| 15 | Importing extracted items into intelligent tasks using context, type, topic and Jira keys | Covered | Intake job: one LLM pass extracts and places (phase 3) |
| 16 | Suggest-new and suggest-existing should be one operation | Covered | Intake decides existing thread / new provisional thread / set aside in a single pass |
| 17 | Completely new schema, new repository, not a fork | Covered | `insights/db/models.py`; this repo |
| 18 | Quick access to current agreements per team member for 1:1s (done, in progress, forgotten) | Covered | Person page (phase 4) |
| 19 | Same summary per project for project meetings | Covered | Label page (phase 4) |
| 20 | New words for task and for the project; it is a planner, not a task manager | Covered | Thread, Mention, Label, Insight; project `work-insights` |
| 21 | Usable immediately; some setup work acceptable, but not days of ramp-up | Covered | Phase 3 is the first usable milestone: intake + Review page |
| 22 | Lots of AI analysis of structure: split overcomplicated threads, parallel vs parent-child | Covered (partly) | `insights_scan` job with split/merge/reparent kinds (phase 5); more kinds on roadmap |
| 23 | Parent-child is enough for dependency; plus non-dependent links | Covered | `parent_id` and `thread_links` |
| 24 | Reports and notifications: slippage, roadblocks, critical items | Roadmap | Item 1 |
| 25 | Goals from review meetings are not Jira tickets but must be tracked | Covered | Thread kind `goal`; review-cycle support is roadmap item 8 |
| 26 | Jira story readiness ladder (manifesto -> triaged -> actionable -> subtask) | Roadmap | Item 7 |
| 27 | Jira synchronisation later | Roadmap | Items 4, 5 |
| 28 | pyproject with pytest, mypy, ruff; no separate config files | Covered | Everything in `pyproject.toml`, including Alembic |
| 29 | Documentation in the Diátaxis structure, browsable on GitHub | Covered | `docs/` ([docs/AGENTS.md](../AGENTS.md)) |
| 30 | Public repo: no secrets or personal data in source or tests | Covered | `insights.example.json`; tests use invented names |
| 31 | Hierarchy of small, human-readable AGENTS.md files | Covered | Root `AGENTS.md` links to per-package files |
| 32 | Ability to re-scan for new insights as new data arrives | Covered | Jobs are rerunnable; mentions dedupe by fingerprint |

## Facts from the ewo database that shaped the design

- 32 tasks, all flat (no parents, no tags).
- 2,183 extracted items: 707 untouched, 733 dismissed, 178 "already done",
  565 attached. 907 had no owner. Only 19 Jira links.
- The user went through the list once and stopped: the cost of a decision
  per item was too high.

The response is to make the AI take the first decision for every mention
(placement is provisional, never pending), to make the user's decision
cheap (confirm is one click, undo is one click), and to keep the number of
new threads small by preferring existing threads and labels.
