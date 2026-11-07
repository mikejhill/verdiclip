# Automatic panel feasibility evidence

This is exploratory qualification after 0.13.2, not a released feature. Product
source is unchanged. No code is injected into other applications.

## First experiment

Head `054baaae1d34ebe4431b528629aa9451973d1407`, native run
[42349024536](https://github.com/mikejhill/verdiclip/actions/runs/42349024536).
All five cases failed the checkpoint and retained their first failure.

- WPF borderless, bordered and nested: native discovery, scroll calls, five
  independent colored-row checks, sibling/horizontal preservation, original
  state/pixel restoration and owned-process cleanup succeeded. Half-view requests
  landed on fractional pixels (206.5 or 126.5), so exact full-width overlap failed.
- Edge: the initial point lookup exhausted eleven ancestors without a
  ScrollPattern. No scroll was issued. The first harness failed to preserve
  ancestor structures on this error, so that run could not explain the cause.
- Full ordinary CI and CodeQL succeeded. These do not qualify the new feature.

## Second experiment

Head `11e115c875b10e71ab9ccf443d3dd9cb62b8ed00`, native run
[42349374566](https://github.com/mikejhill/verdiclip/actions/runs/42349374566).

- Native scrollbar edge strips matched the independent WPF content viewport.
  Whole-pixel request guidance made the nested case pass with a unique 126 px
  overlap and exact restoration.
- Borderless/bordered WPF still failed exact overlap. Retained offset was
  206.00000000000003. Only 78 of 306,153 overlapping color channels differed, by
  at most 3/255, concentrated in rendered text. The next experiment changes
  arithmetic order to avoid unnecessary division/rounding; it does not loosen
  pixel acceptance or manufacture output.
- Edge 156 user-agent receipts and ancestor diagnostics show Chrome shell Panes,
  not webpage Document/content elements. No ScrollPattern was exposed during
  the initial read. This does not prove a general browser incompatibility.

## Next bounded experiment

WPF retains the exact pixel gate with numerically simplified request guidance.
Edge uses a separate **read-only discovery experiment**, not a capture checkpoint:
retain one native UIA client and input epoch, observe immediately, and only after
ordinary no-pattern chain exhaustion observe once more after a fixed one-second
delay. Provider errors and ownership changes terminate. There are no scroll
writes, forced accessibility flags, injected scripts, hooks or input synthesis.
The owned browser window is reduced to fit the hosted desktop at 125% scale.

Chromium documents on-demand and asynchronous accessibility trees:
[architecture](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/accessibility/overview.md).
Delayed readiness is a hypothesis this experiment tests. The design's exclusion
of product readiness warm-up remains unchanged pending evidence and review.
A completed read-only experiment has `diagnostic_completed: true` but never
`checkpoint_passed: true`; neither indicates browser capture support.

## Third experiment and design decision

Head `2ef7694ad1c9516354173696bcd01359ec886372`, native run
[42349794928](https://github.com/mikejhill/verdiclip/actions/runs/42349794928).
Both Edge variants expose only shell panes initially, then the correct inner
ScrollPattern after the single guarded one-second delay. Diagnostic completion
is not capture qualification. Actual browser scrolling remains the next gate.

WPF nested passes. Other WPF cases now report exactly 206 pixels, yet retain the
same 78 channel differences, **all on the first scanline of the new viewport**.
Every other overlap scanline is exact. Independent analysis confirms viewport
boundary clipping rather than fractional-motion error. Repeated arithmetic
changes are not an appropriate remedy.

The next candidate explicitly changes the design: ignore exactly one overlap
boundary scanline at each end only for alignment, require exact full-width
interior equality and a unique displacement, and splice inside the verified
interior. Minimum evidence applies after excluding the two scanlines. Preserve
exact-overlap diagnostics. This is a real acceptance-rule change with a recorded
one-pixel-edge ambiguity limit, not a claim that prior exact-boundary checks pass.
The pure stitcher is tested independently before any product path is wired.

## Fourth experiment

Head `420c35e9b027ea82b02ddd6c58d5e129701f6586`, native run
[42350368338](https://github.com/mikejhill/verdiclip/actions/runs/42350368338).
All three WPF cases now pass the boundary-aware checkpoint, including exact
interior overlap (206 or 126 physical pixels), independent row checks,
horizontal/sibling preservation, exact restored pixels and process retirement.
This is still a bounded top/mid/bottom probe, not full capture qualification.

Both Edge cases discover the correct inner target after settling, but the first
vertical seek resets horizontal position from approximately 47 to 0. The invariant
check fails immediately and the uncertain operation prohibits restoration.
No partial image is delivered; all owned processes retire.

Chromium's native provider implementation corroborates the cause:
[AXPlatformNodeWin::SetScrollPercent](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/ui/accessibility/platform/ax_platform_node_win.cc)
converts both arguments to offsets without a special `NoScroll(-1)` branch.
The negative horizontal percentage is consequently clamped to the left edge.
The next fresh probe passes the exact acknowledged horizontal percentage
explicitly for a horizontally scrollable target, while preserving all readback
checks. No retry or corrective write is attempted in an uncertain session.
The same source computes view size from clipped outer bounds plus scroll range;
this explains the browser provider's metrics without claiming its rectangle is
the actual content viewport. Browser viewport qualification remains open.

## Fifth experiment and complete-sweep gate

Head `fe5f566adddeb1240ac7b15d70f2e25d0c21af50`, native run
[42350720739](https://github.com/mikejhill/verdiclip/actions/runs/42350720739).
Explicit acknowledged horizontal percentages preserve horizontal position in
all five cases. Both Edge variants now perform the actual sparse scroll and
restore exactly. The 125% historical receipt fails only its independent sample
oracle: rounding a fractional content boundary outward included a partially
covered row. The revised oracle samples wholly covered pixels and retains their
vertical phase; offline replay passes all five retained cases. Historical
receipts are not rewritten. None of this qualifies full browser capture.

The next native experiment uses complete WPF top-to-bottom sweeps, repeated
bottom verification, top replay and exact restoration. The independent 96-DPI
fixture oracle checks all 1,457 output scanlines at its known color column,
including every separator and the last row; it is not a full-image golden oracle.
All native writes recheck geometry and deadline. Uncertain frame acquisition or
geometry prohibits restoration writes. Frame and elapsed-time budgets apply to
fallback restoration too. No image is published before all checks pass.

A separately tested pure coordinator models these lifecycle gates but is not
connected to the application's UI or controller. Existing capture remains
unchanged. Browser work is researching full-frame output with static chrome
excluded only from alignment; clean CSS viewport access is not a prerequisite.
Experimental framing rules still have adversarial qualification gaps and are
not included in production. This draft must not be merged or released yet.

## Sixth experiment

Head `dab833f5dd3105d3b949eb6c263ee85180c33b48`, native run
[42352790553](https://github.com/mikejhill/verdiclip/actions/runs/42352790553).
Complete borderless and bordered WPF sweeps both produce the identical
493 by 1,457 pixel image. Every independent scanline check passes, the original
full frame restores exactly, and all owned processes retire. Helper duration is
approximately seven seconds. Ordinary CI and CodeQL pass at this exact head.
Both browser sparse checkpoints pass, including the corrected fractional oracle.

The nested complete sweep correctly fails. In the fifth forward transition,
offsets approximately 504 to 630 pixels, the clipped "24" glyph differs in 27
pixels at the old viewport's final three rows. The existing one-row boundary
allowance does not admit this mismatch. The before/restore frames are identical,
cleanup succeeds, and no composite is published. No tolerance is widened.

A separate bounded clipping diagnostic will compare predetermined nearby offsets
and an interior reference, preserving all observations. It is additional evidence,
not a replacement for the failing full-sweep gate. Absolute integer target
percentages reduce accumulated rounding but cannot make every WPF offset exactly
integral under the provider's floating-point calculation.

## Seventh and eighth experiments

Native run [42353365112](https://github.com/mikejhill/verdiclip/actions/runs/42353365112)
at `9b37fd2d43326ed9ecd56baaab96f0918be50a5c` retains the strict nested
failure and adds fixed clipping controls. Exact offset 503 still produces two
clipped mismatch rows; all neighboring 504 percentage variants produce three.
Offset 505 matches its interior reference exactly. This rules out accumulated
percentage rounding as a sufficient fix. No admission threshold changed.

Native run [42354040558](https://github.com/mikejhill/verdiclip/actions/runs/42354040558)
at `14bd544c90936fd746749f0922ada3cd94d79536` adds independent full-width
reference reconstruction: both successful WPF composites pass all 718,301 RGB
pixels, including labels and seams. The reference uses fixture-state offsets and
whole authored row bands from their most central raw views, never stitcher motion.
The native helper ownership smoke also passes direct producer identity, contained
descendant cleanup, startup failures and owner death before/after readiness.
Ordinary CI and CodeQL pass. These are component proofs, not product integration.

## Separate next experiments

The strict matcher remains unchanged. A fixture-only seam candidate verifies an
exact full-width corridor of max(24, ceil(H/4)) rows centered on the actual splice,
searches every positive displacement up to half a viewport, and requires unique,
observably moving evidence. No color tolerance, adaptive corridor or output
padding is permitted. Native displacement, endpoints, restoration and both
independent output oracles remain mandatory. This is a changed static-content
acceptance contract: sticky or changing content outside the corridor can escape
detection. Explicit tests retain those limitations. It is not a generic browser
solution or a released product policy.

A separate read-only MSAA experiment asks whether native accessibility exposes
real scrollbar geometry for nested browser panels. It reads only a fixed bounded
set of rail points derived from the settled UIA outer bounds, never fixture crop
metadata. Ownership and input guards surround provider calls, all native and COM
cleanup calls share the watchdog, and failure never permits restoration writes.
Diagnostic completion requires unchanged original state/pixels; it does not mean
that a usable scrollbar was found.

## Ninth experiment

Native run [42354624789](https://github.com/mikejhill/verdiclip/actions/runs/42354624789)
at `a8f171cfbf05b4e3ddedfd23cc64a9e7e206cc6b`: all three separate seam
sweeps pass their complete full-width references, including the nested 373 by
1,457 image (543,461 pixels). The original strict nested gate still rejects its
known clipped glyph. Every successful sweep restores exactly and retires its
owned processes. Ordinary CI and CodeQL pass.

Both read-only MSAA cases finish with unchanged pixels/state and clean cleanup,
but their 36 inspected nodes expose only grouping/client roles, not scrollbar
roles. This bounded route did not establish native browser rail geometry. It is
not evidence that every conceivable accessibility route is unavailable.
The native watchdog-release smoke separately proves process death while its API
correctly retains an unconfirmed-cleanup result.

The next inert transport proof uses a current-user-only local pipe, kernel peer
PID verification and asynchronous parent callbacks. Fixed cases cover fragmented
messages, oversized/duplicate/wrong-ID replies, startup/command timeout,
cancellation and a contained wrong-peer descendant. Positive completion requires
clean EOF, zero producer exit and confirmed cleanup. Deadlines are checked at
admission as well as by timers. This experiment has no UIA, images or product UI.

## Tenth experiment and next evidence

Native run [42355411137](https://github.com/mikejhill/verdiclip/actions/runs/42355411137)
at `253876a301d545182d0116537243476c357c9a30` passes all eight inert pipe
cases on Windows with Qt 6.11.2. Actual kernel peer identity is verified, the
contained wrong peer is rejected, positive completion sees clean EOF and zero
exit, and every case confirms whole-job cleanup. Existing panel results remain
consistent, including the separately retained strict nested failure. Ordinary CI
and CodeQL pass. No product transport is enabled.

The next batch records constant-space event-loop/startup/cancellation latency
observations without inventing a responsiveness guarantee. Reversed observation
order produces a null duration and explicit ordering, not a negative or fake zero.
A fresh installed-wheel test runs a copied script outside the checkout, verifies
explicit isolated package origins/versions and poison-path controls, initializes
MSS without Qt in the helper, and retains metadata only for a 16-by-16 sample.

A separate browser collector retains an overlapping sequence of full outer
frames with fixed quarter/half-view offsets and exact endpoint/top/restoration
controls. It produces no stitched result and uses no image heuristic or NumPy.
Independent fixture row/state checks verify raw evidence; complete browser image
assembly remains a separate offline investigation with explicit ambiguity limits.
