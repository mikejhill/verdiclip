# Editor tabs implementation checkpoint

This is an isolated implementation checkpoint, not release qualification.
The application controller now admits reviewed results through the approved
boundary. Capture modules remain unchanged here and belong to the capture worker.
No PR, merge or release is ready until the combined capture contract is verified.

## Ownership

`Workspace` retains `DocumentRecord` and `DocumentPage` independently of host
windows. A record owns an `EditorSession` and delivery metadata; its page owns
the canvas, viewport and tool hosts. Window chrome and actions resolve the
active page. A move retains the same page/session and reverses attachment on
failure. Inline typing is committed before switching; transient gestures stop
without undoing committed edits. Dialog operations pin their originating page.

An explicit close plan obtains every decision before disposing any page.
Cancel retains all tabs/windows, including documents approved earlier in the
plan. Successfully saved files remain saved if a later prompt is cancelled.
New admissions during exit invalidate the close plan. Closing a last tab
closes its host, while the tray process remains alive.

## Shared controller boundary for parent integration

```text
ReviewedCapture(image, title, timestamp, source_kind, completeness, stop_reason)
  -> AppController.deliver_reviewed_capture(result)
  -> existing save/copy/editor output policy, recording actual output success
  -> Workspace.open_document(session, delivery, options,
                             target=..., new_window=False, activate=...)
```

The controller owns one workspace for all editor windows. Its `window_added`
signal wires open-image/settings/about callbacks for initial and detached
hosts; `window_removed` replaces tracking by individual document windows.
Use `workspace.close_all()` for Exit/manual update and `workspace.has_work`
in automatic update idle checks. Capture admission must select the host using
the policy recorded before overlay activation, not incidental overlay focus.

`Workspace.cancel_drag()` is the synchronous, non-destructive quiesce seam.
It releases only tab input and invalidates delayed drops. It does not close
or hide menus, dialogs, tabs or windows. Capture service integration must call
it centrally; no capture path is added inside editor modules.

`activate=False` preserves original native menu/modal focus on delivery. Qt
popup/modal context also suppresses activation automatically. Incoming tabs
are appended in the background while a popup is open. The capture owner must
choose presentation policy for external popup contexts Qt cannot detect.

Scrolling target, tile and repeat-plan state stays private. Tabs consume an
ordinary finished document and never import scrolling or capture modules.
No asynchronous OCR/export feature exists in 0.12.1. `DocumentJobs` supplies only
the ownership protocol: snapshot tickets, content/lifetime identities, exactly-once
completion, acknowledged cancellation and close leases. It starts no workers.
Stale output cannot mark newer edits delivered; edit results defer during close
prompts. Existing modal output operations remain pinned.

`capture.reviewed.ReviewedCapture` is the only shared value type, imported from
the scrolling-owned canonical contract. The former document duplicate is removed;
CaptureProvenance is only a pixel-free projection. The controller
records actual independent save/copy success, keeps failed attempts for explicit
retry, and deduplicates capture IDs and already-successful sinks. Tray-only choices
remain tray-only; pending delivery blocks quit/update. Safe admission defaults to
`activate=False`; the capture owner must explicitly opt into foreground activation
when its restored context permits it. The current plain-capture adapter also uses
this conservative default. Retry uses current output settings, preserves successful
receipts, and does not overwrite a later Save As path or clean a newer edit.

Image saving now uses `QSaveFile`, with destructive direct-write fallback disabled.
Encoding and commit failures preserve an existing destination; delivery is marked
only after commit succeeds. Failed move plus failed rollback retains the page in
the registry/parking widget. `Window > Recover retained tabs` retries presentation;
exit refuses to dispose any document while recovery still fails.

## Capture requirement supersedes the released implementation

Taking a screenshot must close nothing, including VerdiClip and foreign
popups/dialogs. The 0.12.1 popup-dismissal implementation is not acceptable.
Convenience means preserving the user's context, not dismissing it to make
input routing easier. Capture input/backend changes belong to the capture
worker, and the tabs code does not depend on its unvalidated prototype.

## Observable contracts to integrate into ux-contract.md

| ID | Given / When | Then |
| --- | --- | --- |
| UX-TAB-01 | More than one document is admitted to an eligible workspace host | All captures remain available in labelled tabs; switching preserves each document's edits, selection, tool and viewport. |
| UX-TAB-02 | A tab is reordered, detached or moved to another host | The exact editing session, undo/redo, output path and delivered state follow the tab. A failed attachment restores the source. |
| UX-TAB-03 | A tab/window/global close asks about undelivered work | Save / Don't save / Cancel refer to that document; Cancel closes no other document. Enter at rest copies and closes only the active tab. |
| UX-TAB-04 | A user navigates without a mouse | Ctrl+Tab / Ctrl+Shift+Tab, Ctrl+1…9, Ctrl+Shift+PgUp/PgDown, Ctrl+Shift+N, Ctrl+W and the Window menu expose switching, reorder, detach, close and move-to-window. |
| UX-TAB-05 | A warm tab is selected | Activation p95 stays below 50 ms on the declared reference fixture, without flattening, encoding or copying the image. |
| UX-TAB-06 | A completed capture arrives while context is being preserved | No popup/dialog is dismissed; the existing active tab/focus is preserved and the incoming document remains accessible. |

The shared UX-contract file is deliberately left for coordinated integration,
so the capture worker can revise its own acceptance rows without conflict.

## Completed checks before the desktop pause

- Minimum runtime: PySide6/Qt 6.11.2. At an earlier checkpoint, full suite:
  **1,195 passed, 15 expected skips, 94.55% coverage**. Later edits require a
  final full rerun; do not reuse that number as final-commit qualification.
- Native Windows workspace routing: merge, detach, Escape and hotkey
  cancellation passed **4/4** on both Qt 6.11.2 and 6.12.0. These use actual
  Win32 capture ownership and queued native messages. They do not certify
  physical mouse, Narrator or arbitrary mixed-DPI desktop workflows.
- Windows emitted a handled `0x8001010d` COM diagnostic during activation;
  test processes still exited successfully. Keep this visible for follow-up.
- Native lease probe also verified a test popup remained open on cancellation.
- Ownership/close/save integration tests and generated move sequences passed.
- Three keyboard/theme/performance journeys passed, including a p95 <50 ms
  activation assertion over ten 1920×1080 documents and 100 switches.
- Native Windows light/dark gallery images were rendered and reviewed. They
  use existing fictional fixtures and actual Qt widgets, not fabricated UI.
- Formatting, lint and strict type checking pass at the source checkpoint.
- WSL ownership/driver subset passed 20 tests. Targeted mutmut was started,
  but its baseline did not complete in the available checkpoint window; the
  owned processes were stopped and **no mutation score is claimed**.

## Current qualification

See [the validation record](editor-tabs-validation.md) for final offscreen suites,
changed-line coverage, durable mutation results, the reviewed-value integration
mismatch and the unresolved minimum-runtime order-dependent abort.

## Pending native and combined checks

The user resumed capture testing, and the capture worker owns the next desktop
input slot. No native input/UI tests are running in this tabs task. Controller
delivery/close/update-idle integration is local and approved. Physical drag,
mixed-DPI, Narrator and combined no-dismissal capture qualification remain.
The capture owner must invoke `Workspace.cancel_drag()` centrally before acquiring
input, then deliver through the reviewed boundary. Shared UX-contract rows must
be integrated alongside the capture worker acceptance updates.
There is no PR, merge, release or installed-app change in this checkpoint.
