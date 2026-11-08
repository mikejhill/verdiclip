# Bounded scrolling capture

Base: main `a4c68010a3b9d8d8d7d88e60974f1e487f5b773b` (0.13.3).
This replaces neither the normal capture lifecycle nor the paused PR #126.
The small scrolling implementation is connected to the existing tray and
delivery. Integrated head `d95f20dab2d485466597596841d352a919276052` passed
all 13 hosted native cases and 18 unchanged native capture/tab regressions.
The Qt and packaging baseline remains unchanged. Final release-source and main
verification are separate gates; the historical entries below retain the state
and failures known at each earlier milestone.

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

Limits: 25 seconds of transaction work, 80 frames, 40 million output pixels.
The parent kills after 27 seconds or 1.5 seconds after cancellation. A blocked
provider call or forced kill may leave the target scrolled; there is no claim
that killing a client retracts a provider request already sent to another app.
Never launch another helper to restore an uncertain target.

Restore only while input, focus, process, geometry and observed scroll state
still agree with this transaction. After real user activity, leave the user in
control. After cooperative cancellation, restore when still eligible. A failed
restoration is reported explicitly; it is not a successful image publication.
A short unavoidable race exists between observation and a provider call.

## Evidence before integration

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

## Historical isolated-slice review

The isolated slice passed focused source review and 49 portable core, helper
and mocked-native-boundary tests. Real QProcess pipe tests cover crash, timeout,
cancellation, repeated cancellation and cleanup failure. No existing runtime
files are changed. This is permission to gather native evidence, not to enable
the feature or release it.

The initial review reproduction used a 120-pixel viewport and a 50% view size
(240-pixel document), but misleading 32-pixel overlaps produced 296 pixels.
The current candidate rejects this through independent UIA displacement checks.
Full native composition was held until the complete real-app row oracle passed
in run 42444418443 below; exact overlap alone is not a completeness guarantee.

### First real-application findings

Initial exact-head runs are retained at Actions runs 42438853514, 42439089244,
and 42439416156. Real desktop smoke passed; these scrolling jobs failed honestly.
The hosted taskbar covered the first test window. The harness now uses Windows'
work area without changing capture bounds. Notepad then captured two frames,
rejected their overlap, and restored the exact starting percentages. Its saved
pixels include OS scrollbars, motivating the own-HWND client rectangle above.

Fresh Edge exposed only Pane ancestors until ordinary ControlType reads activated
its deeper accessibility tree. After that read and a 0.5-second observation, the
normal document and nested panel exposed ScrollPattern. The first correction
read standard ControlType metadata and refreshed only missing targets. That was
insufficient when a cold tree exposed an outer ScrollPattern before the intended
inner panel; see the later correction below. No browser flag or code injection
was used.


Run 42439957680 verified cold Edge discovery with INFO logging and captured the
installed Notepad document in seven frames (967×2167). Visual review found all
numbered lines 000–119 in order; before/after UIA percentages match. This remains
one real-app smoke case, not universal text completeness qualification.
Edge setup read V0 immediately after requesting V37, while later screenshots
showed line35; the nested setup likewise read stale state. The harness now uses
one combined setup write and bounded read-only polling before recording its
starting state. No runtime tolerance, wait, matching, or deadline is changed by
this diagnostic correction. Browser-drawn scrollbars remain visible in the
saved Edge viewport and unqualified for full-width registration.


Run 42440536721 confirms the setup correction: both Edge cases started from
stable middle positions, with nested horizontal position 34.8837%. The helper
captured its top frame but read unchanged state after its next asynchronous UIA
write. Its immediate restore check failed even though later UIA reads matched
the starting state. Notepad succeeded again with identical result pixels.

The core now observes each single provider write for at most eight 80-ms
read/check slots, inside the unchanged total deadline. Two identical readings
must show advancement in the requested direction and within the existing step
bound; endpoints and restoration require exact requested percentages. Unchanged
reads cannot acknowledge pending motion. Until completion is observed, the
position is unknown and no restoration write is eligible. Input, cancellation,
or deadline during unresolved movement returns failure without another write.
This fixes that safety regression without adding provider-specific tolerances.

Run 42441143626 confirms observed movement/restoration: both Edge cases captured
two frames, rejected full-width overlap, and restored exact starting H/V values.
Notepad again produced identical complete smoke pixels. The next harness-only
step saves the same helper's first two accepted frames and observed UIA positions
for local registration analysis. This test wrapper adds no production logging,
result channel, crop, helper process, timeout, or matching exception. Only owned
test document pixels enter the existing short-lived CI artifacts.


### Correcting the nested-target interpretation

Run 42441991431 retained the actual helper frames. The nested case unexpectedly
captured the outer 976×608 document (H=-1, view=30.099%), while setup selected the
700×420 inner panel (H=34.8837%, view=14.5078%). The outer document moved and
returned; final inner percentages therefore did NOT establish inner capture or
horizontal preservation. Earlier nested final-state results are not qualification.
The incorrect-target frames remain preserved, and no partial image was delivered.

Discovery now requires two consecutive candidates that standard UIA
CompareElements identifies as the same element, within at most three same-point
observations. This permits missing/outer → inner → inner readiness while keeping
the original user-input, foreground window and process eligibility. Provider and
state errors stop immediately; exhaustion never falls back to the outer candidate.
All retained references use the existing COM ExitStack cleanup. The old harness
extra discovery retry is removed. Test-only DEBUG logs preserve ancestor bounds
and candidate comparisons, and the native harness explicitly compares helper
frame dimensions/H/view with the prepared panel and records outer before/after
state. Production logging remains unchanged. Correct inner frames must be
inspected before any compositor change.

The ordinary Edge pair has exactly one displacement with a ≥32-row exact
full-width matching band: 304 pixels, independently predicted by UIA as 304.
Bands at new-frame rows [12,78) and [199,292) support investigating an interior
seam, without masking columns or cropping output. They do not qualify sticky
headers or pixels hidden at every position.


Run 42442650672 confirms the bounded identity sequence in the actual helper:
outer → inner → same inner. It then exposes an actual installed Edge provider
behavior: SetScrollPercent(-1, 0) resets the inner H position from 34.8837% to 0%,
as well as moving V to 0%. The helper correctly stops before any frame and does
not restore over the unexpected horizontal change. Earlier outer-target tests
could not exercise this behavior.

The adapter now supplies the observed horizontal percentage explicitly with the
vertical request. All exact H/V checks remain; no provider-specific exception or
rounding tolerance is added. The next cold run uses INFO, with no additional
DEBUG COM reads. The harness records outer state even when inner restoration
fails. Correct inner frames are still required before changing composition.


### Focused interior-seam candidate

Run 42443103991 uses INFO and verifies the intended 700×420 inner panel, exact
horizontal preservation/restoration, and unchanged outer state for the two-frame
failure path. The correct inner pair has one exact full-width displacement of
210 pixels, independently predicted by UIA. Its matching bands are [12,44) and
[96,183). Ordinary Edge retains its unique 304-pixel alignment. No column mask
or scrollbar rectangle is needed for either pair.

The current stitcher uses those general exact-band semantics, requiring at least
eight distinct rows to exclude uninformative solid bands. Measured advancement
must agree with UIA within max(2 pixels, 2% of predicted advancement); final
height must agree with the provider's extent within max(2 pixels, 2%). The real
Notepad line quantization (288 observed pixels versus 291.394 predicted) is
covered without fitting, stretching, or cropping the output.

A separate first-32-row check requires 95% pixel agreement. It rejects the
concrete broad fixed-header case but is not proof that every hidden pixel was
recovered: a one-pixel overlay can pass. This limitation is visible in the real
inner panel, whose original black focus border covers its first pixel row.
The native oracle compares that original top boundary pixel-for-pixel, reports
both boundary extents, and requires every numbered line in order and every
interior line's full 24-pixel span. It checks final chrome's extent against the
original frame, not its scrollbar appearance, which may change at the endpoint. It never changes the image to fit an
expected height. Residual stitched scrollbar appearance is possible. Sticky,
animated, hidden, or fractional content remains unqualified outside the tested
visible-panel contract.

Portable tests now compare every content row and column across multiple joins
with bottom chrome and a short final step. They reject an 8-pixel fixed header,
a one-column mismatching sidebar, repeated offsets, incompatible advancement,
and accumulated extent error. The native complete-document oracle, exact-head
checks, remaining visibility gap and final coverage gate still block integration.

### Complete installed-app outcome and remaining gates

Run 42444418443 at 0f2dae89 uses the ordinary INFO path and passes all three
installed applications. Edge produces 976×2880 in nine frames, with all 120
numbered lines exactly 24 pixels high. The correct inner panel produces
700×2895 in thirteen frames, with all interior lines complete, the original
one-pixel focus border retained, and fifteen final bottom-chrome rows. The first
visible line has 23 pixel rows; its hidden first row is not claimed recovered.
The inner H/V is restored exactly and the outer document remains unchanged.
Notepad produces 967×2167 in seven frames, visually retaining lines 000–119.
All three before/after viewport images are pixel-identical.

Visual review confirms intact body joins but repeated scrollbar thumbs along
the right edge. These are retained visible chrome, not reconstructed document
pixels. No columns are discarded to disguise them. This visual limitation and
the original focus border need release assessment; successful numbered-content
checks do not make the result indistinguishable from an ideal document render.

The next small safety gate walks top-level windows above the retained root in
z-order, bounded by 512 handles with cycle/missing-root rejection. It rejects any
visible non-minimized, non-cloaked window whose rectangle overlaps the viewport,
including untitled/tool/tiny windows. DWM or rectangle observation failures stop
capture rather than being treated as hidden windows. Conservative transparent
or shaped-window false positives are allowed. Same-window in-app overlays and
changes between observations remain outside this OS-level check.

The existing native harness adds three terminal-outcome cases: cooperative
cancel after a confirmed top frame must restore exactly without publishing;
one-pixel real mouse movement on the disposable hosted desktop must stop without
another provider write or restoration; a rendered owned non-activating 20×20
window between all nine old sample points must reject before any write. The
same test helper records only a final movement count and last requested vertical
percentage. No production protocol or injection is added. Existing deterministic
subprocess tests cover timeout, malformed output, failed start, cancellation,
owner EOF and cleanup. Required full CI, exact-head native interruption results,
UI integration, ordinary capture/Repeat regressions and release gates remain.

Run 42445787024 passes the three original capture cases plus real cancellation
and input interference. Cancellation returns no image after exactly one top
write and one exact restore write. Real mouse input leaves the panel at the
confirmed top position with no image and no subsequent provider write. Full CI
passes 1,931 tests, 96.71% total coverage and 99% changed-line coverage.

The small-cover case stops in its own setup assertion before starting the helper:
Windows' default STATIC handling returns HTTRANSPARENT unless SS_NOTIFY is set.
The harness now includes SS_NOTIFY for its black rectangle, retaining the exact
hit-HWND, foreground and rendered-pixel assertions. This is a one-bit test-window
correction, not a production occlusion change. The failed run remains evidence;
actual small-cover qualification is still required on the changed head.

Run 42446224859 again passes the other five cases. SS_NOTIFY fixes the overlay's
exact hit-HWND check, but its literal-black assertion still fails. To remove
possible pre-event-loop paint uncertainty, the harness calls and checks
UpdateWindow synchronously, preserving the same composition pause and saving
the attempted covered frame before checking its pixels. Production is unchanged.

Run 42446472855 preserves those actual pixels: exactly the intended 20×20 block
is rendered, with all 400 pixels #696969. SS_BLACKRECT is theme-dependent,
not necessarily literal black. The earlier missing-paint
explanation was a hypothesis, not established causality. The harness now compares
the actual before/covered images: all 400 pixels inside the expected rectangle
must change to one uniform observed color, while every outside pixel stays
unchanged. It records that color rather than assuming a platform theme value. Exact hit ownership, unchanged
foreground and between-sample placement remain required. No production check
is weakened; actual small-cover qualification still awaits the changed head.

Run 42446970637 at 393909a1 passes every Edge case, including the fully rendered
20×20 cover. The helper rejects that covered viewport before any provider write;
no image is published and starting H/V remains unchanged. The run's only failure
is Notepad's optional pre-launch PowerShell version read exceeding its unchanged
10-second diagnostic timeout. Notepad itself did not launch on that attempt.

Version metadata is now best effort: expected read/parse failures are recorded
as version unknown with version_error, without retry or timeout widening. Actual
launch, capture and all image/state assertions remain mandatory. Launch failures
now also reach result.json, and an artifact-write failure cannot bypass owned
process cleanup. No production behavior changes in this harness correction.


## Next isolated gate: point-selection handoff (not product wiring)

The test-only `simple_scroll_handoff.py` reuses `SelectionOverlay` and the actual
`TrayIcon.menu`. Its region callback is substituted only inside the harness.
A completed native mouse release or Enter key release selects a physical point
from the frozen window list. The release handler retains that window HWND and
input tick, dismisses every selector, then queues handoff. No helper starts while
a selector, popup, mouse grab or keyboard grab remains, or if input/focus changed.
The helper carries the same two integers in its existing launch arguments and
rejects a mismatched root/tick before UIA discovery. It never chooses a new root
merely because another application became foreground during process startup.

Four additional hosted cases exercise click, Enter release, real input between
selection and helper startup, and a new owned foreground window at that boundary.
Native test clicks/keys target only verified owned tray/selector windows; Edge is
positioned/focused during setup only, never forced to the foreground during
handoff. Positive cases require the complete existing Edge row oracle, the exact
retained child HWND/tick, and restored top pixels. Negative cases require zero
frames, zero provider writes and no image. This proof does not add any product
tray action, change ordinary selection behavior, or touch Capture/Repeat/delivery.
Its first exact-head native result is still pending.


The first handoff run, [42448719257](https://github.com/mikejhill/verdiclip/actions/runs/42448719257)
at `7048cfaadeb584bc90420bf341a1a0f0b021bdb8`, preserved the original six runtime
successes. Actual tray foreground ownership and natural return to Edge were
observed. Click captured all 120 complete rows; both positive composites exactly
matched the previously reviewed output, and both interference assertions stopped
before discovery with zero writes. Two gaps prevent full handoff qualification:

- Enter used stale shared pointer `(589,370)` rather than the actual rounded
  native pointer `(510,356)`. Both lie in the same panel, so the content oracle
  alone could not catch it. The prototype now refreshes the current cursor on
  completed Enter release and asserts the exact intended point; ordinary overlay
  behavior is unchanged.
- The focus scenario passed its assertions but failed owned-application teardown:
  `taskkill /PID 5616 /T /F` returned 255 and an Edge profile journal stayed locked.
  The overall native run failed. Cause is not established; cleanup remains strict
  and unchanged, with stdout/stderr now preserved and report success set false
  when cleanup fails. No timeout increase, ignored failure, or unchanged rerun.


The corrected handoff run, [42449310013](https://github.com/mikejhill/verdiclip/actions/runs/42449310013)
at `30c93b145df2c481e332b5fd6862ba9a1467fe83`, proves exact mouse and Enter point
`(510,356)`, complete content, retained child identity and restored top pixels.
Full CI and CodeQL passed; both harness import-cycle findings were fixed and
resolved. The two startup-interference assertions again withheld all frames and
writes, but their jobs failed forced Edge cleanup. Preserved stderr identifies one
child refusing termination as unsupported; the profile journal remains locked.

The test runner now first posts ordinary WM_CLOSE only when the retained window
belongs to the still-live owned Popen PID. It never closes by title or enumerates
other applications. Graceful wait and the existing exact-owned-PID force fallback
share the original 20-second cleanup budget. Unknown/mismatched HWNDs receive no
message. Process and profile cleanup remain mandatory, and their failures remain
fatal. This changes test teardown only; capture/runtime behavior is unchanged.


### Qualified isolated handoff milestone

Head `0950edc923ceb674416356a94ca46c53c750a45a` passed
[all ten native cases](https://github.com/mikejhill/verdiclip/actions/runs/42449844717),
[full CI](https://github.com/mikejhill/verdiclip/actions/runs/42449844726) and
[CodeQL](https://github.com/mikejhill/verdiclip/actions/runs/42449844742).
All seven review threads were resolved after substantive fixes. Actual mouse and
Enter selection used the exact physical point; tray ownership naturally returned
foreground to Edge. The child retained HWND/input tick, and startup interference
produced zero frames/writes. All five composites matched the previously reviewed
pixels; process and profile cleanup passed. This is an isolated pre-integration
gate, not product or release qualification.

### Reconstructed integration candidate

The cloud workspace reset before the integration was committed. The candidate
was reconstructed in a fresh isolated checkout from verified published head
`0950edc923ceb674416356a94ca46c53c750a45a`, tree
`cab20ddd1941f98fab79c79c9308712b907ac00a`. The former uncommitted tree did not
survive, and its earlier test outcomes do not qualify this reconstruction.

Runtime, harness and most tests were reconstructed from retained source/edit
payloads. Helper shutdown and selective-hotkey removal tests were newly adapted
from their retained coverage requirements; this documentation records current
status rather than claiming an identical recovered tree. Fresh source review,
portable tests, visual review, exact-head hosted native/ordinary regressions,
full CI and security review are required. No merge or release is authorized.

Fresh reconstruction checks passed Ruff, formatting and strict ty. The portable
run passed 1,982 tests with 38 native-only skips and two socket journeys deselected;
the previously restricted local socket/main/feed suites were omitted rather than
retried. Combined coverage was 93.11% (93.98% lines, 89.13% branches with those
omissions). Changed executable-line coverage calculated from the fresh XML was
96.59% for the integration and 98.27% for the full PR. These are scoped local
results; full hosted CI remains required. Light/dark chooser snapshots were
inspected, and the gallery rendered successfully. Linux offscreen ignored native
color-scheme changes in the gallery, so Windows theme evidence remains required.

The first integrated run,
[42453886534](https://github.com/mikejhill/verdiclip/actions/runs/42453886534)
at `e0b1ee75e5dccca9203a354c44e97ec7af063f00`, passed full CI and CodeQL but
failed all three new app modes before scrolling. Its setup incorrectly expected
active-window capture to retain a fixed Repeat rectangle. The existing service
intentionally clears that rectangle so Repeat resolves the current active window.
The harness now seeds fixed-area history through the actual ordinary chooser and
a native click on its owned overlay. It still requires the selected physical
rectangle, the existing document, exact scrolling output and ordinary Repeat.
Runtime behavior is unchanged; no previously failed integration result is claimed
as qualification.

At `87ade9671f730fa71bcdc5c2c2344ae489477d10`,
[run 42454194423](https://github.com/mikejhill/verdiclip/actions/runs/42454194423)
passed the ten isolated cases and the actual configured-hotkey/Enter and Escape
cases. The tray case failed before selection because the test popup was no longer
active when its click was due. The setup returned native foreground to Edge but
blocked Qt event processing; the otherwise identical Escape setup pumped events
while preparing its non-top view. The harness now requires observed native Edge
foreground and Qt ApplicationInactive before opening the test menu, instead of
opening it ahead of pending activation/deactivation events. This is a bounded
setup observation, not a menu retry, arbitrary longer delay or runtime focus
change. A new exact-head run must still qualify the tray flow and ordinary suites.

### Qualified integrated milestone

Head `d95f20dab2d485466597596841d352a919276052`, tree
`566cc5787fe34993269168b6b95fdf7a2fd7451c`, passed
[all 13 native cases](https://github.com/mikejhill/verdiclip/actions/runs/42454539412),
[full CI](https://github.com/mikejhill/verdiclip/actions/runs/42454539403) and
[CodeQL](https://github.com/mikejhill/verdiclip/actions/runs/42454539389).
The tray job passed all 18 ordinary popup/modal and tab-focus tests with zero
skips. Python 3.13 and 3.14 each passed 2,040 tests; Python 3.13 had 97.70% line,
91.98% branch and 98.27% changed-line coverage. CodeQL and zizmor reported no new
alerts; all seven historical review threads were resolved.

Independent source, pixel, editor, cleanup and Windows light/dark visual review
passed. All five isolated positive composites are pixel-identical to 0950edc9.
Actual tray and configured-hotkey/Enter capture produce the same complete Edge
image, add one focused tab to the existing editor and preserve fixed-area Repeat.
Real Escape publishes nothing and makes no restoration write after new input.
Cooperative cancellation restores only when eligible. The bounded setup gate
resolved the observed tray failure; queued deactivation remains a plausible
explanation rather than an established root cause.

The visible scrollbar and boundary artifacts described above remain deliberate
limits, not claims of hidden-pixel reconstruction. They do not remove the tested
supported body sequence. This evidence qualifies the integrated feature source;
versioned release and exact-main checks still run separately before publication.
