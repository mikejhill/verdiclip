# Combined capture foundation and editor tabs checkpoint

Local branch `codex/combined-editor-capture-preview` starts at exact capture head
`50e70355a7173672a5ad64f3a1c092ce314e7b54`. Tabs-only source from fb2f44a was
applied without importing its older capture commits or socket fix. Integration
commits: fa5611ef9580ef054949c69f23679c66d85bf953 and corrected final production
source **ad1fc203709f572b664902acd75a56bbf6055954**.

Capture modules/tests and shell/instance.py + instance lifetime tests remain byte
identical to 50e7035. There is one canonical capture.reviewed.ReviewedCapture and
one socket lifetime fix. The excluded crashing local UIA adapter 5f26ba2 is absent.

## Implemented seam

`AppController.review_scrolling_capture(stopped_session)` returns a nonmodal
ScrollReviewPanel, without showing or activating it. A future qualified capture
coordinator chooses when to present it. No native acquisition adapter, scrolling
tray command or hotkey was enabled.

The controller's ScrollReviews owns session/panel leases independently of window
lifetime. Review emits the canonical object into the existing output policy.
Only actual policy success invokes panel.acknowledge_delivery(capture_id), then
releases the spool. Tray-only success does not require an editor window. The
delivered panel remains visible until the user closes it; acknowledgement itself
does not hide/close existing UI. A document retains independent pixels/provenance.

Failure invokes delivery_failed(capture_id,message); retry uses the SAME accepted
image/ID, honors repaired output settings, and skips previously successful sinks.
Closing accepted failed review UI preserves its lease and controller original for
tray retry. Cleanup failure retains a retryable owner without repeating outputs.
Unaccepted review vetoes exit/idle update until explicit use/discard. Accepted
unfinished output participates in normal Save/Discard/Cancel before application
close; cancellation retains work. A new review arriving during either tab staging
or final host removal vetoes exit. All controller tray/hotkey capture routes cancel
workspace drag before acquisition, without dismissing UI.

## Independent review and final tests

The same separate read-only Codex reviewer independently reviewed exact ad1fc203:
**no confirmed material findings** in the integration. Review covered ownership,
identity acknowledgement, failure/retry/cleanup, close/idle races and drag
quiescence. Independently executed 23 new integration tests on minimum Qt and
48 integration/canonical-delivery tests on current Qt, all passing. An additional
reviewer-injected callback confirmed late review arrival during final window
removal vetoes exit. Earlier tabs source review and its fixed five findings are
recorded separately in editor-tabs-independent-review.md.

Final stable-source Windows/Python 3.13.5 full offscreen runs:

| Runtime | Pass / skip / deselect | Branch-aware coverage |
| --- | --- | --- |
| Qt/PySide6 6.11.2 | 1,455 / 1 / 14 | 94.86% |
| Qt/PySide6 6.12.0 | 1,455 / 1 / 14 | 94.85% |

Changed executable lines versus v0.12.1: **2,672/2,763 = 96.71%**. Ruff,
formatting and strict ty pass. Six selected integration faults were individually
rejected in isolated source copies: missing success acknowledgement, false
acknowledgement on failure, premature release on panel destruction, ignoring idle
lease, missing spool cleanup and retrying the old folder. These runs have actual
behavioral assertion failures; some deliberately broken lifecycle cases also
cause teardown errors. This is 6/6 seeded faults, not an aggregate mutmut score.
Historical 264/318 mutation and 54-survivor triage retain their prior scope.

All tests explicitly use QT_QPA_PLATFORM=offscreen. Imports are pinned to this
worktree's src (reused virtualenv editable installs otherwise point at tabs-only
checkout). Full runs use separate coverage files and local-socket permission.
Native capture/tab modules, global keyboard-hook backend, real registry/shortcut
backends and actual MSS screen freeze remain excluded. No desktop/input/monitor
operation occurred. Final logs, XML and fault replay JSON accompany the bundle.

## Private build and held gates

Workspace-private wheel/runtime bundle:
`editor-tabs-qualification/combined-preview-ad1fc203`, under the task workspace.
Python 3.13.5, Qt/PySide6 6.12.0, mss 10.2.0. Dedicated self-check imports the
installed wheel without QApplication, screen sources, hooks or state creation.
Normal entrypoint executables are removed only from this private runtime.
Manifest **native_ui_ready=false** refuses native launch before GUI creation.

Live scrolling acquisition/backend is unfinished. Native closes-nothing capture
preservation is not fixed/qualified; the locked desktop remains a native-test
blocker. Do not present this as a working end-to-end scrolling feature, or change
the readiness flag to bypass acceptance. Parent must coordinate exclusive native
qualification of final-head capture with Qt menus, owned dialogs and foreign
native popups preserved, real tab drag/detach/reattach/cancellation, clipboard,
keyboard/Narrator and mixed-DPI/multiple-monitor placement. Then Mike verifies the
isolated candidate locally BEFORE MERGE. No normal installation/settings/security
or persistent permissions changed. No publication/merge/release occurred; pushing
this new combined branch is held pending specific parent/user authorization.
