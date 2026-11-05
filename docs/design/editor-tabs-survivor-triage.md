# Review of all 54 targeted mutation survivors

Scope: the completed c77a849 mutation run (264 killed / 318 checked). This review
adds tests; it does not rewrite that recorded score. Exact reconstructed mutation
diffs are in local `.test-tmp/survivor-review.json`. Mutation code was regenerated
from the committed source without coverage filtering because the completed Linux
snapshot was temporary. Each behavior-gap mutation was then applied individually
to an isolated source copy; ordinary pytest used that copy via PYTHONPATH.

| Group | Material gaps | Representation / redundant / dormant | Diagnostics | Total |
| --- | ---: | ---: | ---: | ---: |
| History | 1 | 3 | 7 | 11 |
| DocumentJobs | 0 | 8 | 6 | 14 |
| ImageDelivery.save | 4 | 1 | 7 | 12 |
| TabTransfer | 13 | 2 | 2 | 17 |
| **Total** | **18** | **14** | **22** | **54** |

## Material gaps: tests added, individual mutations now rejected

| Mutant suffixes | Behavior newly checked |
| --- | --- |
| History.execute 9 | A discarded delivered-depth marker must remain safe through another undo and branch. Replacing None with an empty string causes a later string/int comparison error; this was not equivalent. |
| ImageDelivery.save 3, 5, 7 | Missing multi-level output folders are created, rather than only the immediate parent. |
| ImageDelivery.save 24 | JPEG quality changes actual encoded output. |
| TabTransfer.__init__ 12; _rollback 10 | Failed transfer restores the previously active moved tab, as well as a previously active neighbor. |
| TabTransfer.run 14 | Successful move selects the transferred document. |
| TabTransfer._prepare 4, 7, 8 | Detaching preserves the user's disabled-close-prompt choice; omitted/misplaced options may not restore the default. |
| TabTransfer._position 29, 30, 31, 40, 41, 42 | Both far screen edges clamp correctly, including inclusive one-pixel bounds and sign mistakes. This is logical-coordinate policy, not physical DPI certification. |
| TabTransfer._rollback 2 | Failed attachment disconnects destination callbacks before restoring the page; source cursor movement cannot alter destination status text. Automatic Qt reparenting alone would hide the stale-binding defect from ownership-only assertions. |

All **18** mutations were individually rejected by the new assertions in ordinary
pytest. The 17 transfer/output cases are recorded in
`.test-tmp/manual-survivor-results.json` and their per-case logs; the history case
is in `.test-tmp/history-marker-recheck.log`. Failures were behavioral assertion
failures (or the intended repeated-branch type error), not collection failures.
The production worktree was never mutated for these rechecks.

## Representation, redundant and dormant cases

- History.__init__ 14 and execute 11/12 change the start/direction/stride of the
  private content token sequence. Tokens remain opaque, unique per new state,
  stable through undo/redo, and distinct from the initial state. Exact numeric
  values are not a contract.
- DocumentJobs.__init__ 4/6/8, unfreeze 1, and close 7/8/9 change private starting
  counters, falsey representations or terminal increments. A disposed job owner
  cannot reopen; tickets are invalidated and removed. No public lifetime behavior
  changes under the current API. These include type-invalid None mutations; a
  strict type check is a separate gate, not a kill counted in this run.
- DocumentJobs.finish 12 removes an expected-state guard immediately after the
  same state equality was checked on the owning thread, with no intervening yield.
  Retain the production guard as defensive documentation.
- TabTransfer._rollback 11 expands the active-target guard to a redundant None
  selection on a provisional host; the host is subsequently disposed. No public
  page state changes on the existing paths.
- TabTransfer._prepare 3 changes the provisional empty host's delivery fallback.
  Its attached page provides actual output delivery; empty hosts accept no user
  operation during the transaction. This is dormant under current host rules,
  rather than a justification for weakening typed constructor arguments.
- ImageDelivery.save 21 omits the explicit writer format. Tests now verify actual
  PNG/JPEG/BMP encoding, not just readable dimensions/alpha. Its individual
  recheck is recorded separately: Qt can infer format for the named QSaveFile on
  the tested path. Keep explicit format in production; do not assume every
  QIODevice or future runtime has the same inference behavior.

## Diagnostics

History.execute 27–33; ImageDelivery.save 36–42; TabTransfer.run 17/18;
DocumentJobs.begin 3/4, apply 3/4, close 2/3: **22** variations of message case,
prefix, formatting or diagnostic arguments. They do not change the selected
image/history/output/ownership transition. Tests assert useful error meaning and
retained work without freezing cosmetic text or debug-log spelling. These remain
reported survivors; none is silently excluded from the original denominator.

A complete mutmut rerun after integration remains appropriate. The original 83.0%
score and these individually replayed failures are distinct evidence. No adjusted
aggregate score is claimed. No desktop input, native UI or physical monitor settings were
used or altered in this survivor review.
