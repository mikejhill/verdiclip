# Scrolling capture: isolated feasibility slice

Base: main `a4c68010a3b9d8d8d7d88e60974f1e487f5b773b` (0.13.3).
This replaces neither the normal capture lifecycle nor the paused PR #126.
There is no menu, shortcut, release, Qt upgrade, or end-user feature enabled yet.

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

Only after actual-app feasibility and source review will a small selection UI
and existing delivery be connected. Ordinary region/window/menu capture code,
settings, hotkeys, packaging and dependency baseline are untouched.

## Limits and user state

Capture is vertical, finite, and visible on the current unlocked desktop.
The selected app must remain foreground and unobscured. The current feasibility
adapter samples nine viewport points for covering windows; small overlays between
those points can escape detection. This is an explicit pre-integration gap, not a
guarantee that every pixel is unobscured. Any keyboard or mouse
activity, focus change, moved/resized window, changed viewport, missing provider,
uncertain movement, or changed content size aborts. No synthetic scroll notch is
sent at an inner panel's endpoint. Horizontal position is never changed.

The UIA viewport is retained, intersected only with the selected element's own
native client rectangle when it exposes an HWND. This excludes known OS
non-client scrollbars/borders; an ancestor HWND is never substituted. No sidebar
is guessed or trimmed. Exact
full-width overlap is the initial conservative registration method; repeated
rows, sticky chrome, animation, fractional rendering and UIA bounds that include
scrollbars may be unsupported. Real-app results, not fixture-specific profiles,
will determine whether a small general registration improvement is warranted.

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

## Independent pre-publication review

The isolated slice passed focused source review and 49 portable core, helper
and mocked-native-boundary tests. Real QProcess pipe tests cover crash, timeout,
cancellation, repeated cancellation and cleanup failure. No existing runtime
files are changed. This is permission to gather native evidence, not to enable
the feature or release it.

Before integration, also compare registered pixel displacement with UIA extent
and reported advancement. A review reproduction used a 120-pixel viewport and
a 50% view size (240-pixel document), but misleading 32-pixel overlaps produced
296 pixels. Exact overlap alone does not establish correct completeness. Add
this regression and an explicit rounding tolerance when real-app results guide
the registration choice.

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
