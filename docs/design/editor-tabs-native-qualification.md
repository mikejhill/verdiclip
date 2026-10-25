# Tab qualification after exclusive desktop handoff

## Resolved native fixture activation race

Follow-up bounded tracing showed the missing receipt was a queued Qt
`WindowDeactivate`, not a missing WM_LBUTTONUP decoder: WindowDeactivate called
the production cancel guard before the posted release was processed. The native
fixture armed the drag before its two newly shown windows finished asynchronous
exposure/IME/activation processing. No production drag or cancellation code was
changed. The fixture now waits for exposure, drains activation events, and checks
actual GetForegroundWindow plus Qt active-window state before arming. There is no
arbitrary settling sleep, fixed seed, alternate input route or disabled guard.

The same native queue path then passed **4/4 on Qt 6.11.2** and **4/4 on Qt
6.12.0** (detach, merge, Escape, hotkey). Native keyboard/accessibility and injected
rollback/close extension passed **10/10 on each runtime**. Test-owned windows and
input leases were cleaned up; no task-owned test interpreter remained. **Desktop
input released after completion.** These use synthetic Qt/Win32 events, not
physical mouse/Narrator/mixed-display certification.

The new offscreen regression queues WindowDeactivate before a late native
release and checks exactly one cancellation, released lease, unchanged dirty
history, retained document/view and no late detach. Regression + topology tests:
27 passed. The prior full offscreen 1,487-pass runs remain valid for unchanged
production source; this adds one tested contract without a new aggregate claim.

COM diagnostic 0x8001010D also reproduced on both runtimes in a minimal Qt-only
fixture using two QPlainTextEdit windows, no VerdiClip imports, no drag adapter,
no capture/input APIs. Both processes completed normally, active window true,
and cleaned up. The diagnostic was not suppressed. A reusable guarded probe is
`tests/platform/native_qt_activation_probe.py` (plan-only by default). The minimal
reproduction isolates it from the tab release failure; it does not identify the
underlying Qt/Windows COM implementation cause.

Evidence: `.test-tmp/native-trace-1` shows cancellation stack from window.event;
`native-trace-2` shows actual foreground ownership and WM_LBUTTONUP receipt;
`native-tabs-20261025T234034137Z` and `native-tabs-20261025T234050917Z` contain
passing native matrix logs/XML and per-case traces; `native-extended-min/current`
logs/XML contain 10-case results; `native-activation-minimal-min/current.log`
contain Qt-only diagnostic reproductions. Keep original failures below as history.

Live scrolling/backend and capture closes-nothing gates remain blocking. Private
preview native readiness stays false until those combined acceptance gates pass.

## Initial failing native checkpoint (superseded above)

Parent released exclusive desktop input; elevated read-only WTS verified active,
unlocked state (0/1). Ran the prepared four-case native matrix on Qt 6.11.2 at
head `1d910e3473d57aba02ba37e60ab362395875b527` (production source unchanged from
501ec10). **2 passed / 2 failed**: Escape and hotkey cancellation passed; detach
and merge failed. The native capture lease ended, but no drag-release receipt
arrived (`releases=[]`); the move did not commit. The log also contains COM
diagnostic 0x8001010D. This does not yet establish its cause or distinguish a
native adapter defect from a synthetic event/Qt implicit-grab interaction.

All four tests completed and fixture cleanup ran. The Python process exited;
read-only process verification found no task-owned test interpreter remaining.
**Desktop input released.** No further native input was sent; Qt 6.12.0 native
matrix, native rollback/keyboard extension and physical mouse/Narrator/DPI cases
remain unrun pending diagnosis. Native drag/drop is NOT qualified. Normal app,
settings, monitors and security state were untouched. No tool action was denied;
this is a test failure, not an approval bypass.

Evidence: `.test-tmp/native-tabs-20261025T233500493Z/run.log` and `results.xml`.
Keep the private preview readiness false. Resolve the missing native release
receipt and rerun the same bounded matrix before broadening native inputs.

## Offscreen checkpoint and prepared matrix

Checkpoint source: `501ec10e0fe86f5870c45e66dd587b4213e8ad57`. Both Qt 6.11.2 and
6.12.0 full offscreen suites pass **1,487 tests**, 1 skip, 14 deselected; coverage
**94.99% / 94.97%**, randomized seeds 2514902634 / 700042738. Formatting, Ruff and
strict ty pass. The independent read-only reviewer found no confirmed material
finding and independently ran **78 offscreen cases** covering all four new test
files and workspace close/transfer regressions. No native/input action was run.

The new spoken-identity test found stale remaining-tab count after inactive-tab
detachment. The only production delta is a metadata refresh after parking the
removed page. The regression rejects that exact fault replayed in an isolated
source copy; close/transfer staging remains revalidated after callbacks. Durable
evidence is in `.test-tmp/hardware-*.log`, coverage-hardware XML, and
`.test-tmp/accessibility-fault/result.json`. Earlier private ad1fc203 wheel is
superseded by this source fix and remains native-readiness blocked.

Capture worker owns desktop input first. **Do not run native tests until the
parent explicitly hands over the slot.** Default script mode prints a plan only;
no window/input/environment change occurs. Do not use scripts or alternate input
routes to bypass any tool denial, physical Escape interruption or locked session.
No virtual displays/drivers, monitor settings, security changes, hooks or normal
VerdiClip installation changes are needed.

## Hardware-independent evidence

- Synthetic Qt screen geometry covers 100/125/150/175/200/250% scales, negative and
  above-primary origins, mixed screen scales, half-open edges, desktop gaps,
  reordered screens and display removal. Signed native LPARAM coordinates include
  -32768/32767. These prove coordinate policy with supplied topology, not that
  Windows reports that topology or that physical releases map correctly.
- Generated native message sequences prove exactly one terminal receipt, late and
  foreign event rejection and input-lease release using an injected CaptureApi.
- Generated work-area/source-size/drop combinations check complete requested-window
  containment and exact inclusive edges. Removed targets fall back to current
  primary work area. Qt native frame/minimum-size negotiation is not simulated.
- Generated content oracle interleaves edit, undo/redo, delivered-state marking,
  detach/move, before/after-removal failures, close cancellation and current/stale
  job completion. It checks semantic history/dirty state and one live view/session
  per retained document after every operation.
- Accessible tab/close names follow identity/order/delivery through reorder/detach.
  Window-scoped structural shortcuts are named/discoverable. Offscreen text undo
  stays in its inline editor; Ctrl+Tab commits to its origin and focuses the next
  document. This does not certify Narrator or physical focus/input routing.

## Quick native automation (test-owned windows only)

Use the final combined source. Run one interpreter at a time after capture's
exclusive window ends. Both supported minimum Qt 6.11.2 and installed current Qt
6.12.0 remain the baseline; record actual interpreter/runtime and Git SHA.

```powershell
# Safe to inspect now: plan only.
./scripts/test_tabs_native.ps1 -PythonPath ../editor-tabs/.venv-current/Scripts/python.exe

# Only after parent handoff, never concurrently with capture tests:
./scripts/test_tabs_native.ps1 -PythonPath ../editor-tabs/.venv-min/Scripts/python.exe -ExecuteAfterHandoff
./scripts/test_tabs_native.ps1 -PythonPath ../editor-tabs/.venv-current/Scripts/python.exe -ExecuteAfterHandoff
```

The script positively verifies unlocked/active WTS state, uses Windows Qt platform
and this checkout's source, then runs only four native tab cases. It records logs
and JUnit under a new workspace-only `.test-tmp/native-tabs-*` directory and
restores its process environment. It never installs/launches the normal app.
Test documents are synthetic; only test-created editor windows receive Qt press
and Win32 queue messages. No global keyboard registration or real capture occurs.
The tests assert actual SetCapture release, merge/detach or cancellation outcome,
no late-release resurrection, retained image/undo/dirty state and document count.
They are native queue/lease evidence, **not physical mouse proof**.

## Minimal remaining real Windows acceptance matrix

| Check | Actions in isolated, synthetic-document fixture | Required evidence |
| --- | --- | --- |
| Physical reorder/transfer | Three edited captures; reorder one, detach it, merge it into another editor; repeat with the source's last tab. | Same image/annotation/undo/selection/zoom/output identity; one view per document; no accidental close prompt or invisible document. |
| Physical cancellation | Start drag, press Escape; start again and cancel capture/input lease; inject capture-loss only in test-owned windows. | Source/selection restored, no new window, no stuck pointer lease or late drop. Do not press a real global screenshot hotkey until capture preservation is qualified. |
| Mixed DPI and topology | If existing monitors differ, drag both directions, including negative/above origins and work-area edges. If available, move to a current-display gap. | Destination follows actual release and stays reachable. Record existing geometry/scales; do not change settings, disconnect hardware or claim unavailable topology passed. |
| Keyboard/focus/accessibility | Ctrl+number, Ctrl+Tab/Shift+Tab, reorder shortcuts, detach shortcut, Window/menu move target, close Cancel; repeat with inline text. Inspect with already available Narrator if authorized. | Correct document owns shortcuts, inline undo remains local, focus and spoken title/order/dirty/close names track tabs. Offscreen names do not substitute for Narrator. |
| Real clipboard/save/close | Copy edited A while B exists; save A, change it, move it, cancel multiwindow close; retry failed output to an explicitly chosen private folder. | Correct originating document/state receipts, no lost edits, no duplicated successful output. Do not alter system clipboard security or integrations. |
| Capture boundary (capture owner leads) | Existing Qt popup, owned modal dialog and foreign native popup remain present across ordinary capture and final delivery; then review/deliver synthetic scrolling pixels into tabs/tray-only output. | Zero pre-existing Hide/Close, retained owner/action/focus, capture-ID acknowledgement, retry/cleanup. Current production preservation/backend is unfinished; this row remains blocking. |

Stop on physical Escape, unexpected focus/input ownership or any denied action.
Preserve logs and release only test-owned leases/windows through normal fixture
cleanup when authorized. Parent schedules any further input; no workaround route.
Native success alone does not enable live scrolling acquisition, replace the
normal installation, merge or release. Mike verifies the final isolated candidate
locally before merge after the complete capture/backend gates pass.
