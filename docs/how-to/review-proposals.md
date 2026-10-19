# Review structure proposals

`insights_scan` asks the smart model to look at every open thread and its
recent mentions and propose changes to the *structure*: split a thread
that mixes topics, merge two that are the same, move one under another,
mark one done, or drop one that looks abandoned. It never invents work.

## Run it

Open **Jobs** and press **Run** next to `insights_scan`, or:

```sh
insights jobs run insights_scan
insights insight list                 # everything still undecided
```

One run is one LLM call over at most 200 open threads with up to eight
recent mentions each, so it is cheap enough to run weekly or after a big
intake.

## Decide

Proposals appear in two places:

- **Today** lists the first few under *Proposals*; each links to its thread.
- The **thread panel** shows every open proposal for that thread above the
  edit form, with the model's one-line reason. A split lists the proposed
  children and how many mentions move to each.

Press **Apply** to carry it out or **Dismiss** to hide it. A dismissed
proposal is remembered, so the same one is not raised again; if the thread
changes enough for the model to propose something different, that is new.

From the CLI: `insights insight apply ID` / `insights insight dismiss ID`.

## Change your mind

Applied proposals are ordinary changes made by the AI. In the thread's
**History**, press **undo** on any of them, or revert the whole scan with
`insights jobs revert RUN_ID`.
