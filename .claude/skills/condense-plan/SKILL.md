---
name: condense-plan
description: Shorten plan.md by removing finished work and history while keeping everything that affects the remaining work. Run it when plan.md has grown too long.
disable-model-invocation: true
---

Condense plan.md (repo root) so it is shorter, but an AI that reads only this file must still understand
exactly what is left to build and every rule it must follow. Remove what is done or historical; never
remove what affects future work.

Before editing:

1. Copy plan.md to a backup in your scratchpad directory (not in the repo).
2. Read the whole file. Check the git log and the code for every plan, phase or item marked done (✅,
   "done", "as built"). Use the code, not the text, to decide what is really done.

REMOVE:

- History: "Progress (date)" and "Decision (date)" paragraphs, superseded decisions (for example
  LangGraph or Celery), revision logs, how the source documents were merged, and comparison columns
  such as "system_plan says / tools_plan says". Keep only the decision now in force.
- Done work: the step-by-step phases and "as built" sections of finished plans or phases, commit
  tables, test counts, verification numbers, and closed (✅) follow-ups. Replace each finished plan or
  phase with a short "What exists (read the code in <path>)" section. It lists the files, names and
  behaviours later phases depend on (contracts, refusal codes, hooks, conventions), each checked
  against the code, plus any follow-ups that are still open.
- Descriptions of old code ("what exists today", "starting point") that the code no longer matches.
- Schema detail that the models and migrations already hold (column lists, final schema, SQL
  constraint listings, rename maps for renames already done). Keep the rules new code must follow and
  every schema change still to come.
- Design discussion with no effect on the work ("tables that could be merged", "candidates to trim",
  comparisons with other projects), and traceability tables.
- Removed plans: keep one line each, so the plan numbers stay the same.
- Alignment padding in markdown tables, outside code blocks (`|  a   |` becomes `| a |`; separator rows
  become `|---|`).

KEEP, unchanged in meaning:

- Every plan, phase and item that is not done, with its Owner and Phase 1 / Phase 2 lines, its tests
  and its "Done when".
- The two-phase split: scope, interim rules, who does what, contracts, sync points, rules for working
  in parallel, and folder owners.
- Every "Open decision" and its options. Do not choose an option.
- Decisions, invariants and transactions (D1–D28, I1–I21, T0–T10, and any others) with their ids. Keep
  the decision and its rule; drop the long "reason" text.
- Global rules, Definition of Ready, testing commands, the "Not in this roadmap" list, the "do not
  store / do not add" lists, failure cases, and the problem-catalog entries for items still open.
- Section numbers and headings that the rest of the file refers to (for example §P.4 or Phase 9.3).
  When you remove a section, fix or drop every reference to it, and add a one-line note where it was
  ("removed: read the code").

RULES:

- Do not add, change or reinterpret any decision. Only shorten. If you are not sure whether something
  is done or still needed, keep it and list it in your report.
- Keep the file's encoding and line endings (check with `file plan.md`; the working copy is CRLF).
- Do not commit.

Report, in Egyptian Arabic:

- the size before and after, in bytes and in lines;
- what you removed, grouped by type;
- what you replaced with "What exists" summaries;
- anything you kept because you were not sure;
- `git diff --stat plan.md`;
- the path of the backup.
