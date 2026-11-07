# Capture input ownership

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

| Observation | 0.12.0 | 0.12.1 lifecycle |
| --- | --- | --- |
| Popup at the exact freeze boundary | `QMenu` | None |
| Freeze runs on the GUI thread | Yes | Yes |
| Receiver of native Escape | `QMenu` | `SelectionOverlay` |
| Selection active after Escape | Yes | No |

This is an input-ownership defect, not a worker-thread race or an overlay
z-order problem. The older `ModalityGuard` handles Qt's separate dialog
blocking policy: application-modal dialogs become window-modal. That policy
remains in place; it cannot release a popup's input ownership. The 0.12.1 fix
closed the popup before taking its screenshot. The freeze-first change below
retains that input handoff but saves menu pixels before it.

## Lifecycle invariant

`CaptureService` owns the complete request, from input handoff through the
terminal result. Every entry point uses the same boundary.

1. Reserve the session before preparing pixels or dismissing anything. Repeated
   requests share the pending or visible selection, including reentrant provider
   and popup-close callbacks.
2. For global hotkeys, synchronously freeze one desktop frame and copy the target
   geometry/title before dismissing popups or activating selection. Region/window
   picking copies the visible target list; active-window capture copies the current
   foreground target; full-screen capture records the cursor's current monitor.
   Repeat resolves its existing region or current window/monitor at this boundary.
   Tray Capture commands instead retain the hide-before-freeze ordering below,
   so the initiating command menu is absent from their screenshot.
3. Ask Qt to close this application's active popup stack and release its explicit
   widget mouse/keyboard grabs. Ordinary documents and dialogs stay open. A popup
   that vetoes closing produces a visible, retryable failure; its prepared frame
   is discarded rather than shown with blocked input.
4. Queue selection or delivery for the next event-loop turn. This lets the current
   native message, menu action, and nested menu loop unwind. No production sleeps,
   event pumping, window-name checks, or retry-focus loops are used.
5. Check ownership again. If another interaction acquired input during handoff,
   fail without capturing it or forcing it closed. Tray requests prepare their
   frame/targets now. Hotkey requests reuse their saved frame and never refreeze.
6. Create the original per-monitor overlays over one shared selection, using the
   ordinary native pointer. Cancellation during preparation or handoff invalidates
   the saved result. While selecting, focus may move between sibling overlays;
   another window, popup, grab, or application deactivation cancels selection.
7. Selection, Escape, right-click, close, failure, and cancellation converge on
   one cleanup path. Stop queued work and observers, discard prepared pixels,
   close every sibling overlay, and release the session before another request.

An owner-controlled overlay dismissal is distinguished from a user closing an
individual overlay, so cleanup does not produce duplicate cancellations or
turn a successful capture into a cancellation.

## Freeze-first menu behavior

Hotkey capture retains the pixels of a menu that is visible when the request is
handled. The real menu may close during input handoff or overlay activation; it
need not remain interactive afterward. Selection and final output use the same
saved frame, not a reconstruction of the menu. External applications are never
injected with code or given synthetic pointer ownership. No cursor broker,
virtual pointer, global cursor hook, or scrolling implementation is added.

The implementation is an independent reordering of VerdiClip's existing capture
steps. The public behavior of Greenshot was considered as a design reference;
no Greenshot source was copied. This preserves the 0.12.1 selector and its native
pointer. Tab drag previews still cancel at the controller boundary, as in 0.13.1.

Windows registered hotkeys are accepted through both Qt native event categories,
`windows_generic_MSG` and `windows_dispatcher_MSG`. The actual category depends
on Qt's dispatch path; native qualification records the route received from a
real registered chord rather than assuming a particular category.

This change does not claim that arbitrary external menus failed in 0.12.1.
The specific regression discriminator is an application-owned popup that the
old lifecycle closed before freezing. External menus are a non-regression check.

## Validation

The deterministic matrix covers both freeze-first and deferred popup handoff,
rendered menu pixels in final output, copied mutable window targets, cursor
monitor choice before dismissal, established repeat modes, provider/crop errors,
and one-frame coalescing. It also covers popup stacks and dropdowns; main, About,
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
Hotkey-triggered owned tray menus are visible at the freeze boundary but absent
when selection receives input; tray Capture commands remain absent at freeze.
It reserves no global shortcut and uses temporary preferences and fake shell
registrations, so it does not modify the installed app or user launchers.
Diagnostic JSON contains event targets and ownership, not captured pixels.

Headless CI skips only this explicitly native matrix; the deterministic
ownership regressions still run. A known locked/disconnected desktop skips
native capture in ordinary runs, but `--require-interactive-capture` makes
that prerequisite failure explicit. Unknown session state and capture errors
are never treated as a successful test or converted into a skip.

Physical key delivery, a second monitor at another DPI, and arbitrary external
applications remain outside this matrix. Use the
[release policy](../releasing.md#before-merging-a-release-pull-request) to decide
when a critical gap needs an exceptional physical smoke check. These tests establish the
owned-input invariant; they do not guarantee control over Windows' secure
desktop or every external program's input behavior.

## Qualification limits

Deterministic tests simulate the desktop source; they do not establish native
Windows pixels, actual registered-key delivery, or physical pointer behavior.
Require the native freeze-first matrix and released-baseline comparisons before
publication, plus the existing desktop smoke and popup/modal matrix. Preserve
all aggregate latency and coverage gates. Isolated cold-start measurements are
informational: 0.12.1 and the restored 0.13.1 baseline can both exceed the 150 ms
cold-start target. No startup improvement is claimed by this ordering change.
