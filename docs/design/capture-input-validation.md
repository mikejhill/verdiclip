# Capture without changing the foreground

The former service closed Qt popups, released grabs and activated its selection
window. A popup/modal event loop could still own input, producing visible but
unresponsive selection. The replacement separates selection input from Qt's
foreground routing: a finite installer thread publishes semantic commands to
nonactivating, input-transparent overlays. It never closes, hides, activates or
changes the modality of a pre-existing window, and never forwards a selection
click into a target application.

## Contracts

- One queued capture, one input lease, one terminal cleanup receipt. Cancellation,
  stale callbacks, display changes and repeated requests cannot deliver a second
  result. Delivery waits for confirmed hook/raw-input cleanup; failed cleanup
  blocks retry and gives an explicit restart message.
- Mouse/keyboard down/up ownership stays paired. Initial held keys retain their
  original recipient. Escape, Enter and arrows become semantic selection events;
  unrelated keyboard interference cancels. No text is decoded or logged.
- Raw Input provides explicit relative/absolute movement metadata. Relative raw
  deltas do not reproduce Windows cursor acceleration. Unknown injected movement
  semantics refuse selection; the replay profile is explicit and test-only.
- Ordinary input desktop, unchanged foreground and supported recipient integrity
  are checked before readiness and during the lease. Higher-integrity, unknown
  and ambiguous transparent recipients refuse ownership. No elevation, UIAccess,
  security policy, driver or persistent hook is introduced.
- GUI heartbeat, hard deadline, raw-delivery deadline, bounded event queue and
  bounded release draining limit resource ownership independently of GUI health.
- `Capture.preserve_foreground=True` and the service's corresponding property
  require the shell to present delivery and errors without activation. This is
  separate from the canonical `ReviewedCapture` contract. Combined shell behavior
  remains an integration gate.

## Ordinary capture baseline evidence

The exact staged candidate passed **1,533 tests**, one expected platform skip and
15 supplemental desktop tests deselected. Total coverage was **95.35%**; changed
line coverage was **95.15%** against `origin/main`, above the unchanged 95% gate.
Format, Ruff, strict ty and independent Codex review passed.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
uv run pytest -m 'not native_desktop' --cov --cov-report=xml --randomly-seed=231027
uvx diff-cover@10.6.0 coverage.xml --compare-branch origin/main --fail-under 95
```

`tests/conftest.py` explicitly substitutes the replay broker for portable tests;
production has no environment-based fake-input fallback. Fake Win32 DLL fixtures
exercise native ABI bindings, partial setup, malformed raw packets, bounded
registration reads, cleanup failures and integrity queries without accessing a
desktop. Synthetic topology covers removal, identity/scale change and generation
races. Real Qt fixtures retain existing main/About/settings/modal/menu/submenu/
combo windows, checking zero Hide/Close events and unchanged popup/grab/modality
state through semantic drag or cancellation.

The same `test_public_capture_never_dismisses_existing_popup` fails on the prior
service with recorded **Close and Hide** events, then passes on the replacement.
This is an offscreen regression, not a native-input result. Modal journeys assert
preserved visibility and modality while using the new semantic input route; Qt
may still label an overlay blocked in its ordinary input route.

## Composed scrolling entry checkpoint

`ScrollWorkflow` now composes semantic discovery, explicit confirmation,
nonactivating progress, bounded acquisition, restoration, review and canonical
delivery. Ordinary clicks propose scrolling only for a verified semantic
scrollbar. Content clicks retain ordinary window capture; explicit scrolling
also supports a dragged content rectangle inside one verified container.
Neither discovery nor selection forwards a click or grants consent to scroll.
The window handle, process ID and process creation time must match the frozen
selection's generation before a proposal can be offered.

The portable aggregate passed **1,599 tests**, one expected platform skip and
16 supplemental native tests deselected, with randomized seed **24681**.
Total coverage was **95.00%** and changed-line coverage **95.15%** against
`origin/main`. Ruff, formatting, strict ty and independent Codex review passed.
Replay providers exercise the entire service/broker/confirmation/acquisition/
review path; synthetic displays and deterministic pixels require no hardware.

Target, progress and review controls consume semantic pointer/Tab/arrow commands
without taking Qt focus or opening a native combo popup. Progress must fit
outside the captured viewport. Input expiry retains review pixels; a new explicit
capture request resumes a fresh bounded lease. Publication waits for a successful
input cleanup receipt. Failed delivery retries the same canonical object and ID.
After publication the shell owns accepted sessions even if review closes; the UI
labels that action as retaining the capture. Old transferred review callbacks
cannot cancel a later capture. Failed discovery cleanup cannot leave a retry in
an unfinishable discovery state.

Shell integration remains explicit: create `ScrollWorkflow` with a backend
factory, the controller's `review_scrolling_capture` owner factory, private spool
parent and `input_active=lambda: capture.input_active`. Attach it once through
`capture.set_scroll_workflow(workflow)`, expose `capture_scrolling`, and include
`work_changed` in idle/update readiness. The owner must retain accepted pending
captures, acknowledge successful delivery by ID, and present output without
activating pre-existing foreground content. Native backend capability is still
disabled by default; this checkpoint does not grant qualification.

## Pending compatibility and acceptance gates

The activation-based native harness has been migrated. Its common coordinator
waits for broker readiness and confirmed cleanup, checks zero Hide/Close events
and unchanged handles/modality/popups/grabs before teardown, and bounds cleanup
failure independently. Teardown can unwind only explicitly declared fixture
hosts; a foreign application-modal dialog remains untouched. The same coordinator
has 51 portable regressions, including all eight scene setups with drag/Escape,
nested menu/dialog loops, missing/negative cleanup and stale callbacks.

`test_capture_native.py` is now a **native Qt/MSS presentation suite with replay
input transport**. It exercises production broker/worker scheduling through an
explicit in-memory platform, without installing hooks, forwarding packets,
registering global shortcuts or changing the user's clipboard. Posted WM_HOTKEY
and programmatic menu-action callbacks are distinct from physical key delivery
and mouse activation of a native menu. About uses a fixture QMessageBox and
Settings uses the actual SettingsDialog widget; these are fixture-owned modal
hosts rather than implicit ownership of whichever dialog happens to be active.
Trace JSON labels native GUI, replay transport and absence of native hooks.

### Passive surface focus regression

Hosted deferred-readiness traces at `98a5ba2` show the existing popup surviving
setup and overlay presentation, then receiving FocusOut/Close/Hide after overlay
retirement. Capture-free controls pass. A smaller deterministic regression shows
all three broker-driven surface construction paths acquiring Qt's focus window
despite their nonactivation flags; all three fail before the policy change.

Qt's [offscreen platform][offscreen-focus] unconditionally queues a focus change
for shown Tool windows, ignoring those flags; it exempts ToolTip windows.
Output-only capture surfaces now share the passive ToolTip window policy, while
standalone interactive surfaces retain their existing type. This uses a plain
QWidget, without QToolTip expiry timers. The new tests drain platform events
through presentation and retirement and require the original focus window,
popup, handles, visibility, modality and grabs to remain unchanged.

Qt [omits ObjectShow for tooltip presentation][widget-show]; real capture panels
explicitly announce their accessible controls instead. A regression checks the
announcement and button accessible name/role. Completed delivery status remains
passive and mouse-operable; it does not become a keyboard-focused editor.
Keyboard review/retry requires a fresh capture lease. Its explicit Close action
is tested after cleanup without changing the existing popup or foreground.

This diagnoses the synthetic backend's focus-policy mismatch, not a new claim
of Windows qualification. Hosted popup preservation must pass at the replacement
head; native presentation, assistive-technology behavior and actual OS input
remain separate pending gates. Windows [uses nonactivating presentation][windows-show]
for both Tool and ToolTip windows, but source inspection alone is not native proof.

[offscreen-focus]: https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/offscreen/qoffscreenwindow.cpp#L78-L85
[widget-show]: https://github.com/qt/qtbase/blob/v6.11.2/src/widgets/kernel/qwidget.cpp
[windows-show]: https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/windows/qwindowswindow.cpp#L1871-L1876

Only after an authorized unlocked-desktop handoff, run this supplemental suite:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
uv run pytest tests/capture/test_capture_native.py --require-interactive-capture
```

That command does not qualify actual OS hooks, Raw Input devices, physical
pointer behavior, external TrackPopupMenu windows, or UIA scrolling. Those still
need a separate bounded production-broker/native-input matrix with the tool's
input-target protections intact, and observed cleanup of all temporary resources.

## Mutation evidence and limits

The preserved run for `94a980d`, Actions run **40270992197**, completed
successfully: **72.9%**, with **5,101 of 7,002** scoped mutants detected and
**1,901** undetected. A successful workflow is not a claim that every behavior
is tested. Report inspection found uncovered inclusive Raw Input endpoints;
eight primary/virtual-display corner cases now exercise zero and maximum on
both axes. Three reported boundary mutations were recreated in isolated source
copies and killed by these tests. The broader survivor set remains a
test-quality limitation rather than being dismissed as equivalent. The four
earlier targeted consent, cleanup-publication, stale-review and input-ownership
mutations were killed.
Repository mutation thresholds and workflow permissions remain unchanged.

The desktop is locked. No production native broker test has been attempted in
that state. Earlier injected prototype traces are feasibility evidence only.
Supplemental native qualification must cover real Qt/native menus, owned modal
dialogs, raw-input delivery, cancellation, drag, repeated requests and confirmed
cleanup. Physical pointer acceleration/precision and actual hardware remain
honestly distinct from deterministic replay. A transparent hit can safely refuse
capture rather than guess its underlying recipient.

Combined shell integration and native qualification remain pending. Exact pixel
registration deliberately returns a useful partial prefix when overlap cannot
be established; fractional-resampling and dynamic-content compatibility need
further qualification and must never be labeled complete. Native scrolling stays
unavailable by default. Mike must verify the completed isolated build before
merge or release. The installed daily 0.12.1 is unchanged.

## Helper readiness reliability correction

Combined head `cdb948c3` retained the original CI failure and unchanged passing
rerun. Capture-contract seed `3660914482` failed the PowerShell pipe fixture in
9.368 seconds, before accepting READY for the first 120-character fixture
payload. The unchanged retry passed in 3.545 seconds. These elapsed test times
include cleanup; they do not identify launch, pipe or handshake latency.
The original failure's precise cause remains unproven.

The lease previously failed immediately on a Qt timeout callback, without
checking its monotonic due time or transport bytes already buffered for a
separately queued readyRead callback. Three controlled regressions fail before
the correction and pass afterward: buffered READY, buffered operation result,
and early timeout delivery. Real private-pipe fixtures also withhold readyRead
or newConnection delivery while making bytes available, exercising both stages
of the corrected drain path.
Delayed connection notifications tolerate an already drained queue without
raising or accepting the peer twice.

The correction keeps the existing absolute 8-second cold-start cap and 3-second
operation allowance. It adds no launch allowance and does not enlarge the
binding's 12-second preparation cap. The timer is precise and checks monotonic
time. A due timeout performs one nonblocking buffered read, then rechecks the
process, request, phase and deadline generation before failing anything. Direct
completion callbacks may submit another request without the old timeout
terminating it. No wait, nested event loop or automatic action retry is added.
[Qt documents timer scheduling and precision limits][qt-timer-accuracy]; these
remain event-loop deadlines, not hard real-time scheduling guarantees.

Diagnostics retain at most 32 relative-time lifecycle stamps, byte counts and
cleanup state. Fixed stderr markers distinguish script entry, pipe connection
and runtime preparation; arbitrary stderr text, paths, endpoint names, payloads
and provider content are discarded. The protocol-only PowerShell fixture saves
the immutable snapshot in JUnit, including on failure. A local fixture trace
observed OS process start at 7ms, script entry at 267ms, READY at 355ms and
confirmed retirement at 407ms. These are parent-observed callback/marker times;
child progress can precede delivery. They are one protocol observation, not a
latency guarantee or native scrolling qualification.

A separate 2-second cleanup watchdog reports unconfirmed retirement while
retaining the busy slot, process, pipe and resource callbacks. Only a finished
signal or observed NotRunning state can confirm retirement. Early or stale
watchdog callbacks cannot shorten this interval. Cancellation, invalid protocol,
partial readiness and failed cleanup remain failures; preservation assertions
are unchanged. A future genuine cold-start overrun still fails safely. New
phase evidence is required to attribute such a recurrence; a green rerun alone
does not establish that the historical hosted failure is resolved.

[qt-timer-accuracy]: https://doc.qt.io/qt-6/qtimer.html#accuracy-and-timer-resolution

## Combined scrollbar interaction case (prepared, not qualified)

The native broker matrix now includes one opt-in scrollbar case:

```powershell
./scripts/test_capture_broker_native.ps1 -ScrollbarOnly -ExecuteAfterHandoff `
  -PythonPaths '<explicit isolated Qt 6.12 Python executable>'
```

It reuses the owned WPF scrolling fixture and existing broker driver journal.
The external supported driver must discover and bind that exact window before
capture can start, then provide one guarded click at its real scrollbar. The
script does not generate input or automatically acknowledge driver binding.
The case checks native input receipts, the actual semantic scrollbar hit,
automatic complete delivery, every RGBA pixel, retained popup identity and
visibility, restored scroll offset, and broker/UIA/fixture retirement. It does
not qualify physical RawInput, global hotkeys, arbitrary providers or mixed DPI.
The exact-pixel fixture requires 100% scaling. Only the test composition enables
the native provider; product readiness flags and installed settings are unchanged.

Offline validation: 127 tests passed, with nine native cases skipped; lint,
formatting, type checks, PowerShell parsing and cleanup-wait clipping passed.
Independent review addressed partial-cleanup failures and incompatible lease
budgets. The acquisition lease is bounded to 40 seconds, the owned case process
to 65 seconds, and supervisor cleanup waits to the original 90-second cutoff.
Timeout, forced termination and uncertain cleanup remain failures.

The November 4 native attempt failed after 25.52 seconds at the 20-second
supported-driver binding deadline. The fixture published 183 immutable visible
snapshots with unchanged process/window identity. Supported window discovery did
not return it; no binding acknowledgement, broker worker, input or capture
occurred. Fixture exit and cleanup were confirmed. Discovery call timestamps
were not retained, so filtering versus discovery timing is unproven. Do not
infer product acceptance or bypass discovery using the fixture's reported HWND.

The available desktop input API activates its selected window before input;
its compatibility with retaining an existing popup also needs verification.
No unchanged laptop retry is ready. Existing hosted Windows Qt presentation
jobs and the separate owned-window capability canary do not provide this case's
external guarded input driver. Native click ingress, semantic hit classification,
popup preservation and full-panel fidelity together remain acceptance gates.

### Disposable hosted input lane (manual opt-in)

`CI` has an optional `hosted_scrollbar` workflow-dispatch input, false by default.
It selects a standard `windows-2025` x64 VM, Qt 6.12, read-only repository contents,
no persisted checkout credentials, no cache and no artifact upload. The source SHA
must match the workflow's exact commit. Ordinary PR/push jobs cannot enable native
input. No new runner, display driver, RDP session, privilege change or trust change
is involved.

This lane uses native `EnumWindows` plus the existing fixture's PID, nonce title,
visibility, immutable snapshots, foreground ownership and point ownership. It is
separate from the local supported Computer Use binding: it must never be used to
work around a missing or denied window on a user's desktop. Environment checks
are safeguards, not independent machine authentication; the workflow's isolated
hosted-job placement is the execution boundary.

After binding, the test starts the real broker and rechecks the exact live worker,
original case deadline and popup state immediately before one `SendInput` batch:
move, left-down, left-up. It does not activate a window or retry a click. A partial
batch is a failure; only a known inserted down receives a matching release attempt,
and uncertain release remains an explicit failure. Windows UIPI restrictions are
retained. Native hook receipts, actual UIA ancestry, pixel equality and cleanup
are still required; the acknowledgement alone cannot pass the case.

The input and payload layouts follow Microsoft's [SendInput documentation][sendinput]
and [INPUT structure][input-structure]. These are synthetic native events, not
physical RawInput or hardware parity. The existing 90-second case supervisor and
five-minute job timeout remain independent bounds. No hosted execution result is
claimed by the offline/mock checks. Dispatch this lane only as a reviewed,
explicit isolated qualification run; no laptop session is needed.

[sendinput]: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
[input-structure]: https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input
