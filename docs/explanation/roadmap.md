# Roadmap

Ordered backlog of work that is agreed in principle but deliberately not in
v1. Items near the top unblock the most value. Each item names the wish it
serves in [requirements.md](requirements.md).

v1 itself is delivered in phases: (1) scaffold, (2) core services, API and
CLI, (3) notes reader, intake job and Review page, (4) Today, Person and
Label pages, (5) `insights_scan` with split/merge proposals. Everything
below starts after phase 5.

## 1. Reports and notifications

Daily/weekly report of critical items: threads past their due date, threads
not mentioned for N weeks (slippage), threads marked blocked (roadblocks),
agreements that were never confirmed as done. First as a page and CLI
command, then pushed through the Discord integration. Wish 24.

## 2. Discord integration

Deliver reports and let the user mark threads done or ask "what is open with
Robert?" from a private Discord channel. The config section and interface
exist; the listener is not implemented. Wish 24.

## 3. Google integration

Calendar: show tomorrow's meetings on Today and link each to the person or
label so the prep view is one click away. Email is out of scope until there
is a clear use. Config section reserved.

## 4. Jira Server/DC read-only fetch

Fetch summary and status for linked keys with the Server/DC REST API
(`/rest/api/2/issue/{key}`); fill `jira_links.summary/status`. Lets the Label
page show which threads are already done in Jira. Wish 4, 27.

## 5. Jira sync and seed from JQL

Create threads from a JQL query (e.g. the top of the Kanban board) and keep
their status in sync one way (Jira -> insights). Never writes to Jira.
Wish 27.

## 6. Jira ticket-type awareness

Know the difference between a story and a subtask and render the hierarchy
Jira already has, instead of asking the user to repeat it. Wish 26.

## 7. Story readiness ladder

A per-thread "readiness" field mirroring how the team works: manifesto ->
triaged -> actionable (one to two weeks, clear acceptance criteria) ->
subtasks (a day or less). An insight kind that proposes the next step for a
thread. Wish 26.

## 8. Review-cycle goal support

Goals created in the twice-yearly review meetings get a review period and a
"before next review" due date; the Person page shows progress toward them.
Wish 25.

## 9. Planning horizons

Mark threads as this-week / this-month / this-quarter and show a horizon
view. Wish 2.

## 10. Notes change tracking

Remember the git revision each mention was seen at; when a note changes,
re-extract only that note and show what moved. Wish 14.

## 11. Note-writing conventions

A short how-to on writing notes so intake works better: Jira keys inline,
one agreement per line, a name at the start of an action. Possibly a lint
job that reports notes without any detected owner. Wish 4, 15.

## 12. More insight kinds

Beyond split/merge/reparent: "this thread looks done", "these two people
own the same thing", "this label has no activity", "this goal has no
threads under it". Wish 22.

## 13. Full insights list page

The design keeps insights contextual, but a filterable list of all open
insights is still useful once there are many. Wish 11.

## 14. MCP server

Expose read-only threads/mentions to an MCP client so an assistant can
answer questions about the plan. Dropped from v1 to keep the surface small.

## 15. Docker and Postgres

Container image and a Postgres option for running on a server. SQLite is
enough for a single user on a laptop.

## 16. Import ewo decisions

Read the old ewo database and mark mentions already dismissed or done
there as set aside, so a first intake over the same notes does not resurface
them.
