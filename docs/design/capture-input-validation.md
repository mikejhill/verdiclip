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
has 45 portable regressions, including all eight scene setups with drag/Escape,
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
