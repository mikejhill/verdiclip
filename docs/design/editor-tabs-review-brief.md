# Independent review brief — editor tabs

Status: local review preparation. Parent arranges the independent Codex review.
This document is not independent approval. No PR or publication has occurred.
User verification of an isolated combined build is required before merge.

## Review target

Combined source/build candidate: `8616e39542c150304c6723978c539a6945dfd76f`.
Earlier tabs-only hardening: `722affd3ece807dccfb7e16367436648689f14b5`.
The canonical contract is integrated; see editor-tabs-canonical-integration.md.
The private bundle manifest keeps native_ui_ready=false; only self-check ran.
Base: v0.12.1, `a1c6bbe488284cf5f5b4ed7a8b1e30181e0f62ed`.
Earlier implementation commits: `cb586888` and `c77a849`.

The final review must use the exact combined PR head after the scrolling-owned
canonical ReviewedCapture change and tabs migration. Re-read the final diff and
rerun applicable checks if that head changes. Do not approve a comparison against
an earlier tabs-only snapshot or an experimental capture-input worktree.

```powershell
git diff --stat v0.12.1 HEAD
git diff v0.12.1 HEAD -- src/verdiclip/editor src/verdiclip/document
git diff v0.12.1 HEAD -- src/verdiclip/shell/controller.py src/verdiclip/shell/instance.py src/verdiclip/output/delivery.py
```

Review capture changes
separately from their exact combined base; experimental native input code must not
silently enter the review target. Parent owns publication approval and PR creation.

## Intended result

Multiple captures group in an eligible editor host while retaining Chromium-like
reorder/detach/reattach workflows. Stable document ownership keeps edits, history,
selection, viewport, delivery and jobs with the page. Moves retain the same session;
failed moves restore or retain recoverable presentation. All-document close
preflights decisions, so a later Cancel does not partially close earlier work.

Controller admission preserves chosen save/copy/editor combinations, records actual
success, retains failures for retry and deduplicates completed sinks. Safe capture
admission defaults to background focus. Atomic output replacement preserves an old
file when encoding or commit fails. Jobs are only an ownership protocol, not a new
OCR/export feature or worker framework.

## Highest-value review questions

1. Trace every terminal document transition: close tab, close host, Exit/update,
   transfer commit, failed source removal, failed destination, rollback failure,
   recovery failure, delayed drop, cancelled input and new capture during close.
   Can any path lose the last reference, dispose unapproved work, duplicate hosts,
   leave stale signal bindings or allow updates while documents/jobs remain live?
2. Verify page-bound tool hosts and window action routing. Test inactive-tab state,
   per-document delivered markers, typed text commit, selection, zoom/viewport,
   saved path, repeated undo/redo branches and original-image retry after editing.
3. Inspect close reservations and job completion together. Cancellation request is
   not acknowledgement; committed output after cancellation is reported honestly;
   stale completion cannot clean new edits; edit results cannot race a close prompt.
4. Challenge the transfer transaction without relying solely on QWidget reparenting.
   Source and destination active tabs, callbacks, failed provisional hosts, exact
   retained document identity and recovery refusal during Exit must be correct.
5. Reconcile the canonical `capture.reviewed.ReviewedCapture` contract: one class,
   stable capture_id, aware created_at, required COMPLETE/PARTIAL and optional stop
   reason. Mint identity once at acceptance. COMPLETE refers only to requested
   extent. Do ordinary and scrolling producers preserve evidence and use the same
   controller entry point without importing experimental input implementation?
6. Inspect capture quiescence centrally. `Workspace.cancel_drag()` releases only
   its own lease and invalidates queued drops. No screenshot path may hide/close
   any popup/dialog/tab/window. Foreign native popup focus cannot be inferred from
   Qt's activePopupWidget alone. Background admission is deliberate until safe
   foreground policy is supplied by the capture owner.
7. Review native driver limitations against actual final-head evidence: terminal
   outcomes, WM_HOTKEY/Escape/capture-loss cancellation, coordinate source and
   physical-to-logical mapping, frame geometry, multimonitor islands and high DPI.
   Offscreen policy tests are not physical desktop certification.
8. Review SingleInstance._read's lifetime fix independently: fully read the client,
   transfer ownership to the application, schedule normal deletion, then emit data.
   No nested event pumping or premature socket.close. The original source and both
   Qt versions reproduced the crash; the child-process test uses no widgets.
9. Check the verification launcher cannot forward to or modify the normal app:
   separate instance name/settings/styles/log/output paths; no actual shell
   registrations, updater or default hotkeys. An ordinary __main__ launch is not
   sufficient isolation. Keep persistent recovery/pinning/reopen out of this scope.

## Evidence and limits

- Latest randomized Windows offscreen suites: 1,348 passed on each Qt baseline,
  1 expected skip and 14 explicit desktop/native integration deselections each.
  Minimum Qt/PySide6 6.11.2: 94.95%; current 6.12.0: 95.01% coverage.
- Changed executable lines: 1,871/1,926 (97.14%) against v0.12.1.
- Formatting, Ruff and strict ty pass. See editor-tabs-validation.md for exact
  runtime/order records, deselection scope and the reproduced socket teardown fix.
- Original targeted mutation run: 264/318 caught, 54 survivors. All 54 classified in
  editor-tabs-survivor-triage.md; new assertions reject all 18 material mutations in
  individual source-copy replays. No revised aggregate score is claimed.
- Earlier native drag cases passed 4/4 on minimum/current; earlier actual light/dark
  widgets were rendered and reviewed. These predate final integration. Physical
  drag, Narrator, mixed DPI and combined no-dismissal capture remain final gates.
- Warm tab activation p95 measured 2.28 ms for ten 1080p documents/100 switches.
  The earlier 4K copy timing outlier and handled COM diagnostic remain documented.

Do not waive a failing gate based on an earlier passing commit, image or seed.
Any review finding should include the concrete trigger, affected invariant, severity
and a reproducible test where feasible. Parent coordinates independent review;
Mike supplies local verification, not manual source-code review.

## Suggested PR description after integration

Captures previously required separate editor windows. This change groups them in
movable tabs while retaining multiple windows, with document-owned edit/output
state, reversible transfers and all-document close cancellation. Reviewed capture
delivery preserves existing output choices and keeps failed outputs retryable.

Validation: replace this paragraph with exact final combined-head results, native
acceptance evidence and independent review outcome. Explicitly list any remaining
gate as a blocker. Keep the PR draft until those checks and the isolated local
verification build are ready. Merge/release waits for Mike's local verification.
