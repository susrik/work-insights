# Run intake over your notes

Intake reads notes changed since the last run, extracts mentions and places
each one on a thread. It needs `llm.api_key` and `notes.enabled` set.

> Intake arrives in phase 3. This page describes the intended use so the
> rest of the docs can link to it; the commands below will work once the
> `intake` job is registered.

## From the GUI

Open **Jobs**, press **Run** next to `intake`. The log streams live. When it
finishes, open **Review** to see what it did.

## From the CLI

```sh
insights jobs run intake
insights jobs run intake --full      # ignore the last run, re-read everything
insights jobs run intake --no-wait   # return immediately, follow with `jobs log`
```

## Reviewing the result

Each mention placed by intake is `provisional`. On the Review page:

- **Confirm** if the placement is right.
- **Move** to another thread, or to a new thread, if it is wrong.
- **Set aside** if it is noise, already done, or not actionable.
- **Undo** any single decision, or **Revert run** to undo everything the
  run did.

Threads intake created are also provisional until you confirm them; a
confirmed thread confirms all its provisional mentions.

## Re-running

Mentions are deduplicated by note path and summary, so re-running over the
same notes does not duplicate anything. Set-aside mentions stay set aside.
