# Capture input ownership

Historical account of the **0.12.1 popup-closing implementation**. Its closure
and activation policy is superseded by the no-dismissal requirement. Do not use
the lifecycle below as implementation guidance for the replacement. Current
contracts and evidence are in [capture-input-validation.md](capture-input-validation.md).
The native presentation harness now uses replay input and waits for broker
readiness; OS-hook/hardware qualification remains separate.

## Failure mechanism

A capture overlay being visible or active does not imply that it receives
input. Qt routes native input to its active popup stack independently of
window activation and dialog modality. In 0.12.0, the global hotkey handler
called capture synchronously while a tray menu could still own that stack.
The overlay appeared, but an Escape sent through its native `QWindow` reached
`QMenu` instead. Escape dismissed the menu and left selection running.

The regression was reproduced against an untouched `v0.12.0` source snapshot
with the real tray context menu, a Windows `WM_HOTKEY` posted to the test's own
GUI thread, and real MSS desktop capture. The trace showed:

| Observation | 0.12.0 | Fixed lifecycle |
| --- | --- | --- |
| Popup at the exact freeze boundary | `QMenu` | None |
| Freeze runs on the GUI thread | Yes | Yes |
| Receiver of native Escape | `QMenu` | `SelectionOverlay` |
| Selection active after Escape | Yes | No |

This is an input-ownership defect, not a worker-thread race or an overlay
z-order problem. The older `ModalityGuard` handles Qt's separate dialog
blocking policy: application-modal dialogs become window-modal. That policy
remains in place; it cannot release a popup's input ownership.

## Lifecycle invariant

`CaptureService` owns the complete request, from input handoff through the
terminal result. Every entry point uses the same boundary.

1. Reserve the session before dismissing anything. Repeated requests share
   the pending or visible selection, including reentrant preparation calls.
2. Ask Qt to close this application's active popup stack and release its
   explicit widget mouse/keyboard grabs. Ordinary documents and dialogs stay
   open. A popup that vetoes closing produces a visible, retryable failure.
3. Queue preparation for the next event-loop turn. This lets the current
   native message, menu action, and nested menu loop unwind. No production
   sleeps, event pumping, window-name checks, or retry-focus loops are used.
4. Check ownership again before freezing. If another interaction acquired
   input during handoff, fail without capturing it or forcing it closed.
5. Freeze once and create the monitor overlays over one shared selection.
   Cancellation during preparation invalidates its result.
6. While selecting, observe Qt ownership changes. Moving focus between sibling
   overlays is allowed. If another window, popup, or grab takes input, or the
   application deactivates, cancel rather than leave a frozen selection.
7. Selection, Escape, right-click, close, failure, and cancellation converge on
   one cleanup path. Stop queued work and observers, close every sibling
   overlay, and release the session before another request can begin.

An owner-controlled overlay dismissal is distinguished from a user closing an
individual overlay, so cleanup does not produce duplicate cancellations or
turn a successful capture into a cancellation.

## Validation

The deterministic matrix covers popup stacks and dropdowns; main, About,
Settings, and other modal windows; every capture mode; explicit grabs;
queued/reentrant requests; cancellation; vetoed popup closure; capture errors;
focus loss; overlay close; sibling cleanup; and recovery. Existing shared
selection tests retain cross-monitor and mixed-scale coordinate coverage.

Run native Windows validation on an unlocked desktop:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
uv run pytest tests/capture/test_capture_native.py --require-interactive-capture
```

The native matrix uses the actual controller/tray menu, native Windows hotkey
message delivery, actual main/About/Settings widgets and modal event loops,
and real desktop capture. It tests Escape and drag completion for each host.
It reserves no global shortcut and uses temporary preferences and fake shell
registrations, so it does not modify the installed app or user launchers.
Diagnostic JSON contains event targets and ownership, not captured pixels.

Headless CI skips only this explicitly native matrix; the deterministic
ownership regressions still run. A known locked/disconnected desktop skips
native capture in ordinary runs, but `--require-interactive-capture` makes
that prerequisite failure explicit. Unknown session state and capture errors
are never treated as a successful test or converted into a skip.

Physical key delivery, a second monitor at another DPI, and arbitrary external
applications still need the release checklist. These tests establish the
owned-input invariant; they do not guarantee control over Windows' secure
desktop or every external program's input behavior.
