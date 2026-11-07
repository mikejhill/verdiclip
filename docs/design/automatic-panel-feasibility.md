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

## Eleventh experiment

Native run [42356727601](https://github.com/mikejhill/verdiclip/actions/runs/42356727601)
at `4ee73c4fd191b20d0c654b4ba7a48e1763993eea` passes both complete
browser raw-sequence collectors, the fresh installed-wheel runtime proof and all
eight inert pipe cases. The separate seam sweeps continue to pass; the original
strict nested failure remains visible. CodeQL passes. Ordinary CI found a new
Windows-only test-fixture newline mismatch, not a runtime failure: text writes
translated LF while the synthetic wheel retained original bytes. The subsequent
fixture-only correction preserves exact-byte validation and explicitly tests
rejection of newline-only installed mutations.

The installed proof covers a fresh wheel in an outside-checkout Unicode-path
Windows x64 Python 3.13.17 environment, isolated/no-site helper startup without
Qt, MSS capture, poisoned search-path controls and confirmed owned cleanup. It
does not qualify every installer, Python version or frozen distribution.
Measured helper launch took 2.050 ms first-in-process and 1.673–1.816 ms later;
maximum heartbeat/poll gaps were 11.023/11.239 ms with 10 ms timers. Cancellation
requested termination promptly and confirmed cleanup after 6.137 ms in that
case. These QCoreApplication observations are neither a cold-cache guarantee
nor a rendered-GUI responsiveness qualification. A separate lifecycle-worker
experiment must cover slow launch and cancellation before product integration.

Offline browser research retained every full outer frame and used only native
state plus observed pixels for alignment. Both datasets contain 16 frames and
13 distinct poses. A conservative candidate produced full-width 334 by 1,476
and 417 by 1,844 experimental images with source-row provenance. Its rail model
requires every pixel to match a static background or rigidly translated thumb;
every modeled background pixel had at least nine distinct revealing poses.
An independent review reproduced and corrected an omitted-frame accounting
hole, verified the retained frame/provenance counts and rejected small
low-contrast mutations previously missed by the earlier candidate.

A separate fixture-only body reference found zero differences over 458,955
pixels at 100% and 715,260 integer-interior pixels at 125%. Fractional boundary
pixels at 125% are not covered by that reference. The candidate still has
explicit stationary-cap, boundary-row and semantically ambiguous widget
limitations. Static appearance does not prove that hidden content is recovered.
Total decoded-memory admission and realistic adversarial content qualification
remain required before adopting any of this scratch implementation. None of
these results enables a product scrolling command or qualifies a release.

The next isolated proof assigns helper, pipe and timers to one lifecycle thread,
with queued immutable terminal evidence to a visible GUI. Fixed synthetic stalls
exercise cancellation before creation, after native return and after the startup
deadline. Heartbeat and paint witnesses must occur inside each stall; original
GUI cancellation timestamps remain attached to retirement measurements. An
external owned-job supervisor bounds the experiment and rejects forced teardown
as successful proof. These delays release the GIL and do not make a genuinely
stuck native API interruptible.

Unused runtime-manifest/bootstrap primitives derive identity from already-loaded
packages and installed distribution metadata, independent of installer brand.
A separate installed-wheel case exercises the exact bootstrap prefix with a
fixture-only ready/wait suffix, retaining independent wheel attestation and the
original MSS-capture case. This is not an enabled provider or evidence for every
uv tool/uvx/Python runtime. Native results for these exact additions are pending.

## Thirteenth experiment and retained failures

Native run [42358072647](https://github.com/mikejhill/verdiclip/actions/runs/42358072647)
at `274652aa368161b34d1748f53cd8e858e2f7655c` passes the exact installed
bootstrap-prefix case alongside independent wheel/MSS capture and owned cleanup.
Ordinary CI and CodeQL pass. The lifecycle proof hits its unchanged 45-second
external watchdog without case receipts, and one Edge 100% collector hits its
unchanged three-second startup limit. Both retain failure and confirm owned
cleanup. Other modes retain their prior results, including the original strict
nested failure. Neither timeout is a successful qualification.

A real offscreen Qt reproduction identifies a lifecycle test-harness problem:
both completion signals arrive, but QApplication.quit invokes the active window's
intentional close veto, so its event loop never exits. The correction exits that
event loop only after the existing terminal-plus-thread-finished gate. Active
window close still cancels and vetoes closure. A subprocess regression covers
both behaviors; bounded phase and exception diagnostics preserve future failures.
The original native child had no diagnostic output, so this is a reproduced
mechanism rather than a claim that its absent stderr established the cause.

The browser startup cause remains unknown: the first flushed event follows
imports and output setup, and no earlier diagnostic was retained. A fixed
stdlib-only wrapper now records bounded import phases and exceptions in a
sibling artifact. Those records do not satisfy or extend the original watchdog.
There is no automatic retry, broader native permission or injected code.

The next separate native collection covers authored conversation-light100 and
nested-table-dark125 fixtures. Raw native acquisition runs before independent
self-scroll reference pages at predetermined oracle positions. Physical geometry,
DPR, width, theme, horizontal position and extent must match. Completed native
calls, cleanup errors and final deadlines are checked before accepting process
exit. Collection success is explicitly not raster-comparison or product success;
fractional terminal phases cannot later be rounded away to manufacture a match.
Existing colored fixtures and ordinary capture remain unchanged.

## Fourteenth experiment

Native run [42359107328](https://github.com/mikejhill/verdiclip/actions/runs/42359107328)
at `465d420fd79f8d52d07f62325bf5a66d2c4084ef` completes all five inner
lifecycle cases, including GUI paint/heartbeat witnesses during synthetic stalls,
expected cancellation and all thread exits. The outer supervisor still fails:
one owned process remains immediately after the launcher exits. Its identity
was not recorded, so console teardown is not an established explanation. The
next supervisor retains read-only same-job kernel identity and exit witnesses,
and permits only natural empty-job evidence within the original 45-second total
deadline. Forced cleanup never qualifies success.

Both realistic matrix cases stop before helper launch because the caller used
the command-line flag spelling instead of the existing internal experiment token.
The narrow correction uses the unchanged internal allowlist; a regression now
executes real routing rather than mocking the router. No watchdog or retry changes
accompany it. Ordinary CI, CodeQL and both original browser raw sequences pass.
The new matrix references and complete outer lifecycle cleanup remain unqualified.

## Sixteenth experiment and independent conversation reference

Native run [42360697287](https://github.com/mikejhill/verdiclip/actions/runs/42360697287)
at `831ed63df11b0d0f89758c51f920a26cdf849099` restores ordinary CI and
CodeQL success. The conversation/light case collects all seven independent
reference tiles. Unchanged offline full-outer matching produces 320 by 1,823
pixels. Independent comparison verifies all 543,004 body pixels and all 613,137
overlapping tile comparisons exactly, including tile-edge rows and the complete
301-column body. Full-width top and bottom endpoint appearances also match. No
fractional rounding or edge exclusion is used for this 100% case. This is one
owned fixture, not the complete product journey.

The table/125% reference still rejects its rounded requested endpoint rather
than claiming exact fractional coverage. Reference readiness and geometry work
continue separately. The outer lifecycle witness now identifies an owned console
host while alive, then observes the same pinned handle signaled with zero exit
and the job empty about 11 ms later. A redundant postmortem image lookup fails.
The next correction retains that already verified identity for the same handle,
while still requiring fresh exit evidence. There is no process-name exemption.
Initial identity failures and uncertain exit observations remain disqualifying.

A separate owned-browser experiment compares bounded WM_MOUSEWHEEL scrolling with
read-only UIA/native evidence. Native scroll messaging is not foreign-code
injection. This diagnostic never moves the cursor, switches input methods or
uses UIA-write restoration after an uncertain wheel action. Four fixed cases
cover top/middle starts and interior/endpoint behavior; independent fixture state
audits ancestor, sibling and horizontal preservation. Its native results are
pending and do not authorize a product input strategy.

### Residual image metadata versus natural retirement

The outer lifecycle-proof supervisor now separates optional residual executable
metadata from mandatory retirement evidence. It attempts an image query once per
same-job pinned process handle, at the existing query-limited rights. A failed
image lookup remains visible as `image_status: unavailable` with the exact API
and Win32 error. It is not retried after exit or through another API, and no
executable name is inferred or exempted. Available identity is retained only for
that same pinned handle.

Natural cleanup still requires successful handle acquisition and same-job
validation, successful zero-wait and exit-code queries, signaled pinned handles,
an empty final owned job, successful inspection-handle closes, and admission
within the original 45-second absolute deadline. These failures remain fatal.
The receipt labels `image_metadata_required_for_cleanup: false` and lists
`metadata_unavailable` separately from cleanup `errors`. Direct provider PID and
kernel-image authentication are unchanged and remain mandatory.

This is an explicit revision of the diagnostic contract, not a reinterpretation
of prior runs. The run at `69639e4` remains failed: its initial image lookup was
denied and the earlier supervisor stopped without proving natural retirement.
Its forced cleanup does not qualify it. The prior observed `conhost.exe` lifetime
at `831ed63` motivates avoiding redundant postmortem metadata reads, not a
process-name exception. A fresh reviewed native run is required to qualify this
revised outer-supervisor contract.

The next reference collector retains complete owned-root screenshots rather than
cropping on an exact border color. Three equal rasters and matching static DOM
states must settle within two seconds, under the existing process/native-call
watchdogs. It retains first/last images and bounded observations on failure.
Raw entry selection has no DOM input. Independent reference telemetry records
initialized fractional content-box/border geometry and the actual authored bottom
position; unresolved phase still rejects. Received DOM sequence ordering is not
atomic pixel/state attestation, so this proves only the authored static case.

All four first wheel runs at `69639e4` stop on backwards discovery timestamps:
the discovery-start value overwrote its later event emission time. The producer
now owns the event timestamp and records discovery start separately. A regression
exercises the actual emitter, JSONL reader and unchanged watchdog validator.
Existing bounded discovery settling was already enabled and is not extended.
