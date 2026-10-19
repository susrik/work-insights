# Run intake over your notes

Intake reads notes changed since the last run, extracts mentions and places
each one on a thread. It needs `llm.api_key` and `notes.enabled` set.

## From the GUI

Open **Jobs**, press **Run** next to `intake`. The log streams live. When it
finishes, open **Review** to see what it did.

## From the CLI

```sh
insights jobs run intake
insights jobs run intake --full      # ignore the last run, re-read everything
insights jobs run intake --no-wait   # return immediately, follow with `jobs log`
```

## What intake does with a note

For each note it sends the LLM the folder's `AGENTS.md` chain, the team
roster, the catalogue of open threads and the numbered note text, in one
call. The model returns the open items it found and where each belongs:
an existing thread, a new thread, or set aside. Two deterministic rules
run on top:

- a mention citing a Jira key that is linked to exactly one open thread
  goes to that thread, whatever the model said;
- labels whose hints match the note path or Jira project are added to the
  thread the mention lands on.

People found under `notes.people_dir` are added to the roster
automatically; a note inside a person's folder is attributed to them
unless the model named someone else.

## Reviewing the result

Open **Review**. Mentions are grouped by the thread they landed on, new
(provisional) threads first; set-aside mentions sit in a final group.
Pick an intake run from the selector to see only what that run did.

- **Confirm** a mention if the placement is right; **Confirm thread** (or
  **Confirm all**) accepts a whole group.
- **Move** to another open thread, or create a new thread from the
  mention, if it is wrong.
- **Set aside** if it is noise, already done, or not actionable;
  **Restore** brings a set-aside mention back as unplaced.
- **Dismiss thread** drops a provisional thread; its mentions become
  unplaced.
- **Revert run** undoes everything the selected run did. Single decisions
  can be undone with `insights changes undo <id>`.

## Re-running

Mentions are deduplicated by note path and summary, so re-running over the
same notes does not duplicate anything. Set-aside mentions stay set aside.
