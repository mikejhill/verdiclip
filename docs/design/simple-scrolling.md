# Bounded scrolling capture

Scrolling capture uses the existing chooser and delivery path, without a
configuration dialog. Ordinary capture and Repeat remain separate. Native
qualification applies to the exact tested source, never a later cleanup tree.

## Small boundary

- `platform/scrolling.py`: native UIA ScrollPattern discovery from a physical
  point and guarded native reads/writes. The nearest vertically scrollable
  ancestor is selected. No wheel input, application code injection, foreground
  forcing, class-name profiles, or content-specific runtime behavior.
- `capture/scrolling.py`: bounded top-to-bottom sequence, ordinary bitmap overlap
  registration, and conditional restoration. A separate `ScrollResult` carries
  one complete image or a failure. It is not `Capture.area` and cannot poison the
  existing Repeat history.
- `capture/scroll_helper.py`: one owned process per transaction, one terminal
  result and temporary PNG. Cooperative cancel, bounded kill, and stdin EOF on
  parent death handle blocking providers. No per-frame IPC or second helper.

- `capture/scroll_selection.py`: a separate frozen point chooser retains the root
  HWND and completed release tick before closing selectors. One owner keeps the
  helper and temporary Escape registration until terminal retirement. Cancelled
  queued results never publish; failed synchronous shutdown retains ownership
  until the later real terminal signal retires the child.

Tray → Capture scrolling area and the configurable Ctrl+Alt+PrtSc shortcut use
this chooser without another configuration dialog. Completed images use the
same save, clipboard and editor/tab delivery; scrolling metadata never invents a
physical screen rectangle. `CaptureService`, `SelectionOverlay` algorithms and
ordinary Repeat history are unchanged. Settings and manual updates wait for
retirement; automatic updates remain busy. A second scrolling request cancels.
Actual Escape is user input and never promises a restoration write. Temporary
file cleanup warnings preserve delivery errors and retained-editor recovery
callbacks rather than replacing their notifications.

The actual-controller native matrix adds `edge-app-tray`, `edge-app-hotkey` and
`edge-app-escape` to the existing ten isolated cases. These use a real existing
editor, production chooser/helper/hotkeys and delivery, then verify ordinary
Repeat. The tray job also runs the unchanged native capture popup/modal and tab
baseline suites. Passing portable tests alone does not qualify these boundaries.

## Limits and user state

Capture is vertical, finite, and visible on the current unlocked desktop.
The selected app must remain foreground and unobscured. The adapter checks
whole-viewport intersections against higher visible top-level windows and keeps
nine viewport ownership samples. Same-window overlays and changes between
observations remain outside this practical check. Any keyboard or mouse
activity, focus change, moved/resized window, changed viewport, missing provider,
uncertain movement, or changed content size aborts. No synthetic scroll notch is
sent at an inner panel's endpoint. Requests preserve the observed horizontal
position; an unexpected provider change stops capture.

The UIA viewport is retained, intersected only with the selected element's own
native client rectangle when it exposes an HWND. This excludes known OS
non-client scrollbars/borders; an ancestor HWND is never substituted. No sidebar
is guessed or trimmed. Registration requires one globally unique displacement
with an exact, textured, full-width band of at least 32 rows. It joins inside
that band, preserving earlier pixels above and later pixels below the seam.
This replaces intermediate bottom chrome with later visible content while
retaining the original top boundary and final bottom boundary. Repeated or
unreliable pixels are rejected rather than assigned an arbitrary displacement.

Limits: one immutable 35-second transaction deadline, 80 frames and 40 million
output pixels. New traversal phases and forward/top-seek setters stop at
33 seconds. An already-issued step may finish its existing proof within the
remaining time; the reserve is at most two seconds, not guaranteed restoration
time. A cutoff before the setter retains only previously proven recovery state.
Once the setter may have executed, the existing unresolved-write rules apply.
All pixel, item, extent, terminal and restoration checks remain mandatory.

The setter admission callback runs after the adapter's blocking preflight and
fresh window/input guard, immediately before SetScrollPercent. It checks the
transaction's deadline/cancellation and only then marks the write uncertain.
Restore uses the hard deadline and ignores only cooperative cancellation. A late
provider check, composition or cleanup cannot acknowledge or publish success.
The 35-second bound adds headroom, not a performance fix or completion guarantee;
the sequential 80ms observations and existing stride remain unchanged.

The parent schedules retirement against its original monotonic 37-second
launch deadline, preserving the two-second lifecycle margin. First cancellation
can only shorten that deadline to at most 1.5 seconds from now. Repeated cancel
cannot extend it. Decode, cleanup and queued publication reject expired success
even if the Qt timeout callback was delayed. A stalled GUI event loop cannot
promise wall-clock process termination. A blocked provider call or forced kill
may leave the target scrolled; there is no claim that killing a client retracts
a provider request already sent to another app. Never launch another helper to
restore an uncertain target.

Restore only while input, focus, process, geometry and observed scroll state
still agree with this transaction. After real user activity, leave the user in
control. After cooperative cancellation, restore when still eligible. A failed
restoration is reported explicitly; it is not a successful image publication.
A short unavoidable race exists between observation and a provider call.

## Required qualification

Hosted Windows tests use installed Edge on ordinary local HTML and installed
Notepad on a text file. Start in the middle, preserve horizontal position,
capture top to bottom, compare ordered Edge document landmarks, and verify restore.
The Notepad case is a capture/restoration smoke test with saved pixels for content
review, not an independent automated completeness oracle.
An Edge inner scrolling element inside a scrolling page checks outer position
at the endpoint. Unsupported applications fail the qualification honestly.
Save screenshots and brief JSON outcomes for review, without a proof service.

Portable tests cover exact output, final short overlap, repeated-content
ambiguity, fixed sidebars, wrong dimensions, provider failure/no movement,
changed state, cancel, deadlines and subprocess cleanup. Native runs also prove
actual screen capture and keep the existing popup/modal and tab gates. A green
synthetic test suite alone does not qualify native capture or a release.

## Representative owned fixtures

Explorer tests create 160 harmless files in a temporary folder and verify its
new HWND, PID and exact canonical path before setup and cleanup. Only that
verified fixture window is closed; the desktop shell is never killed.
`--taller-explorer` changes only the outer-height cap to 1000 physical pixels,
clamped to the measured work area. Header/body/rails and H0 are checked from
actual geometry, not assumed from the window size. `--no-hbar-explorer` requires
observed H=-1 and actual List begin/finish calls, not generic-path success.

The display helper selects only offered Settings choices, then independently
checks monitor scale and target DPI: 125% is 120 DPI; 200% is 192 DPI. Owned
setup runs in a bounded child. Failed setup does not qualify product execution.
The bounded owned View-menu receipt preserves setup failures; it cannot replace
the original failure or authorize another menu action. The recorded fixtures
without a horizontal bar now pass at 125% and 200%, without qualifying positive
H offsets.

The ordinary tiny helper test compares a real uninstrumented helper completion
against a fresh same-window traced reference, full visible file inventory and
exact permitted restoration. It complements the per-frame full-row/native-item
oracle; neither result alone proves every provider or every layout.

See [native scrolling qualification](scrolling-diagnostics.md) for retained
coverage, limitations and historical evidence. Closed inverse, four-write
geometry, exact-region and profiling experiments are not release infrastructure.
