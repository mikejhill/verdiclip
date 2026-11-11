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
retaining the original top boundary and final bottom boundary. A repeated local
band is disambiguated only when exactly one candidate has an exact entire
overlap. Repeated entire overlaps and unreliable pixels are rejected rather than
choosing a displacement near the provider percentage.

A stable fixed top boundary can remain once in the output when original
same-coordinate prefix pixels and the entire moving overlap independently prove
one displacement. Ordinary registration is preferred. The fixed-prefix proof
retains an interval of possible boundaries and intersects it with each later
pair, rather than guessing whether coincidentally identical rows belong to the
header or moving content. Changes inconsistent with that retained proof and
ambiguous complete overlaps stop capture. Pixel-identical alternative
explanations cannot establish the semantic boundary. The full image width is
retained. Any small bottom perimeter exemption requires independent bounded
solid-edge and stationary corner evidence; it is never selected to make a
preferred displacement match. This captures the visible content sequence,
including its original top boundary; it cannot reconstruct content permanently
hidden behind an opaque overlay.

A generic first uncommitted join with a unique full-width band but a changing
top may
make one bounded observation pass before restarting. From the rejected view,
the two additional probes advance a quarter viewport and then three quarters,
keeping the same intended final span and three views while varying their spacing.
Three settled forward frames must share the same leading full-width pixels,
independently of their registration matches. Coinciding moving rows can still
make the observed prefix too long; a later prefix change remains a fatal refusal.
Capture then returns to the original top and requires its
exact position and original raw pixels. After that restart, every later frame
must preserve the observed prefix, and every accepted join must have a unique
full-width displacement proof and an exact textured band below it, with the
existing 5% top check
applied to the moving rows below that prefix. No rail or corner crop is inferred.
Short pages, changed extents and unsuccessful exact returns cannot use this
path. The pass cannot repeat or reset an established prefix. A banner that
appears and then stays unchanged is indistinguishable from a collapsing header;
this mode preserves the originally observed beginning, not necessarily the
page's latest state. It retains the ordinary band matcher's limitations and does
not claim a complete moving-overlap or semantic-header proof.

Generic full-width registration first retains its existing unique-band and
unique complete-overlap rules. A remaining ambiguity can also be resolved when
one candidate exactly explains the union of every original qualifying maximal
band from every candidate. Each rival is compared with that candidate in their
pairwise common observable row domain; a third displacement cannot shorten it.
The rival must retain a qualifying band there. The candidate must have at least
32 contiguous supported rows with eight distinct full-width rows that the rival
does not explain exactly. Even isolated exact rival rows interrupt this exclusive
band; they are not discarded for being too short to qualify on their own.
Original evidence outside each pairwise domain is never discarded: a candidate
unable to observe or explain any such evidence fails. Equal support, independent
moving regions, missing shared evidence and untextured exclusive rows remain
ambiguous.
This is set containment, not a longest-band score or preferred displacement.

Only after independent selection must a containment winner agree with a finite
integral native displacement within one millionth of a pixel for floating-point
arithmetic. The ordinary two-percent tolerance and missing-extent path cannot
admit this proof, including before a top-boundary observation pass. The seam must
lie in a tail-compatible original qualifying band of that winner. Exclusive
support selects the displacement; the seam itself needs exact full-width equality
and the existing tail consistency check, not local exclusivity. Native Header,
footer, rail and List routes, edge-assisted matching, and
the stronger fixed-prefix complete-overlap proof do not use this fallback.
This remains a visible-content band proof, not proof of global rigid geometry:
changes outside the qualifying evidence retain the ordinary matcher's limits.

A second generic ambiguity proof recognizes one bounded rendered track on the
right edge before considering any displacement. Measurement starts at the outer
column and stops at the first uniform inner boundary; it cannot search past a
gap or exceed eight columns at 96 DPI (scaled upward proportionately). A uniform
background must contain exactly two stationary end marks and one identically
shaped thumb translated downward. End marks remain within sixteen rows of their
respective edges. The thumb requires a solid center of at least 32 rows and
bounded mirrored caps. Extra marks, changing shapes and wider regions fail.

Only that independently measured strip is exempted from complete-overlap
equality. Exactly one displacement must match every remaining content pixel
and retain a textured, exact full-width band for the join. The output keeps the
entire width, including the track. This path uses the same strict native
confirmation and tail consistency checks as containment; it cannot bypass a
changed content pixel, infer a fixed header, or enter the native List routes.
The geometry recognizes a rendered shape, not an application-specific scrollbar
identity. An identically drawn edge decoration is indistinguishable, so this
remains a visible-content capture proof rather than a semantic chrome detector.

For a generic panel with unchanged extent and no verified native Header/List
geometry, ambiguous pixels, absence of an eligible registration band or a
rejected body boundary below an unchanged observed prefix can trigger at most
four smaller-step attempts. Each attempt returns to the last accepted position
and requires its entire raw viewport to match exactly before reducing the
rejected interval. Ambiguity and body-boundary attempts keep three quarters,
then two thirds, then one half of the preceding rejected interval. Thus an
uninterrupted sequence visits 75%, 50%, 25% and 12.5% of the original interval.
An absent band always keeps three quarters, providing more overlap without
immediately placing a
scrollbar thumb inside a short fixed top. No rejected pair contributes output.
For a generic unprefixed full-width ambiguity that neither containment nor
track evidence resolves, the smallest spacing between original rival pixel
displacements can propose the next acquisition distance. It is capped at the
ordinary three-quarter retry interval and must be positive and smaller than the
rejected interval. This can observe the boundary where a pinned component starts
moving with the document. The rival spacing never selects an alignment or changes
a seam. The rejected frame remains rejected; the exact anchor return and the
fresh frame must satisfy all existing checks. Fixed and learned prefixes do not
use this hint. A wrong hypothesis consumes the same bounded retry allowance.

Every new frame counts against the existing frame and time budgets. Only these
typed failures permit recovery; changed prefixes, input, geometry, provider and
other reported failures still stop immediately. Containment-based body refusals
must pass exact native displacement confirmation before any retry. Each accepted
join must independently satisfy
the unchanged unique-pixel proof. No provider percentage chooses an ambiguous
match, and a failed or uncertain return never permits a retry.
A top refusal on a smaller first step can seed the same one-time observation
pass, using that actual position and raw frame. It cannot reset an established
prefix or start learning after a committed join.

The ordinary eight-sample, 80ms settlement path is unchanged. Only eight reads
exactly equal to the pre-write state on a generic traversal allow one read-only
native target re-resolution using a temporary UIA client in the existing COM
apartment. Two hit-tests at the original point, 0.5 seconds
apart, must identify the same nearest scrollable element, role, bounds and native
owner. The original interfaces and input/window/display baselines are retained;
a newly nested scroller is rejected. The original retained ScrollPattern must
then acknowledge movement through the normal two-equal-read proof, within at
most eight further polls. Fresh-interface values alone never acknowledge a write.
The refresh and confirmation share a two-second admission budget from settlement
entry and the original transaction deadlines. No-progress observations cannot
consume the restoration reserve. State, direction, extent, input and geometry
checks remain active. This never issues a second setter or permits restoration
over an unresolved write. A blocking native call is not preemptible by this local
observation budget; the helper's outer deadline remains necessary.

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
