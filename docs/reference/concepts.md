# Concepts

The vocabulary used in the UI, the API and the code.

## Thread

A unit of work, an agreement, or a goal. Threads are the things you plan
with. Every thread has:

- `kind`: `work`, `agreement` or `goal`
- `status`: `open`, `in_progress`, `blocked`, `done`, `dropped`
- `priority`: `critical`, `high`, `normal`, `low`
- an optional owner (a person), parent thread, due date, labels and Jira keys
- `provisional`: true when an AI job created the thread and you have not
  confirmed it yet

Threads form a tree through `parent_id`. The tree is the only dependency
notion: a child is part of its parent. Separate from the tree, any two
threads can be *related* through a link that implies nothing.

## Mention

A place in a note where something actionable was said. Intake extracts
mentions from notes and puts each one on a thread. A mention has:

- `path`: the note file, relative to the notes root (file only, no line)
- `summary`, optional `excerpt`, `kind` (`action`, `question`, `risk`,
  `deadline`), optional owner and due date, Jira keys found in the text
- `state`: `provisional` (placed by AI, not yet reviewed), `confirmed`
  (you agreed with the placement) or `set_aside` (noise, already done, not
  actionable; stays in the database so a re-scan never resurfaces it)
- `thread_id`: where it sits, or empty if unplaced

Mentions are deduplicated by a fingerprint of path and normalised summary.

## Label

A grouping: a project, topic or team. Labels have optional hints:
`note_path_prefixes` (a thread whose mentions come from a matching note
folder gets the label) and `jira_projects` (a thread linking a matching
Jira project gets the label). Children inherit their parent's labels at
creation time.

## Person

A team member, or you (`is_self`). Not an account: nobody logs in. A person
has an optional `notes_dir` (their folder in the notes tree; notes in it are
about them) and aliases for name matching.

## Change

An append-only record of every mutation to a thread or mention: who did it
(you or an AI job), what field, before and after. Any change can be undone;
a whole job run can be reverted.

## Insight

An AI proposal about structure: split this thread, merge these two,
reparent that one. Shown on the thread it concerns with Apply and Dismiss.

## Job

A named background task: `intake` (read notes, create mentions, place
them), `insights_scan` (propose structure changes), `housekeeping`. Every
run is recorded with a live log.
