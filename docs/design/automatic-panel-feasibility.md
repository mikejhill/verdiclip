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

## Eighteenth and nineteenth experiment evidence

Native run [42362003577](https://github.com/mikejhill/verdiclip/actions/runs/42362003577)
at `b7f1be8` passes the reviewed outer lifecycle contract. A residual image query
returns access denied once and remains recorded as unavailable. The same pinned
owned handle then exits with code zero and the job becomes naturally empty about
10.5 milliseconds later. All five inner lifecycle cases pass. This qualifies that
inert proof under its explicit contract, not the new packaged channel.

Both full-root matrix collections pass. The conversation reference remains exact.
The table's full-outer candidate still lacks qualified fractional origin/phase
proof, so collection success is not accepted as raster correctness. Its genuine
half-device-pixel outer edges must be preserved, not cropped or dismissed as an
unsupported browser case.

Native run [42362721743](https://github.com/mikejhill/verdiclip/actions/runs/42362721743)
at `cb517c9` passes both wheel interior cases, including exact restored fixture
state and pixels. Both endpoint cases reject motion during a capture. Paired
read-only observations identify vertical-only target changes while structure and
other state remain unchanged. This demonstrates sampled motion, not its internal
cause. These failed cases remain failed evidence.

The next bounded wheel experiment may discard direction-consistent vertical-only
crossings only while a successfully sent write is awaiting acknowledgment. It
retains uncertainty, records the discarded pair and does not issue another write.
Three identical complete samples must span at least 0.2 seconds before admission.
Identity, geometry, nonvertical changes and direction reversal still reject. The
original two-second settling deadline is never restarted or extended.

The same experimental batch adds read-only UIA identity checkpoints on owned
stable WPF and Edge fixtures, plus reference-only render-widget origin, fixed
marker and high-entropy version evidence. Stable identity observations do not
prove replacement detection. Geometry projections remain unqualified until
native evidence and numeric/source assumptions support the independent oracle.
None of these experiments enables scrolling capture in the product.

## Twentieth experiment: endpoint propagation and origin limits

Native run [42363755912](https://github.com/mikejhill/verdiclip/actions/runs/42363755912)
at `c04bd9d` passes both stable identity cases and both wheel interior cases.
The latter restore exact initial state and complete panel RGB. Both endpoint
negative cases now reach target bottom, then deliberately send another down
notch. That propagates to the outer scroller; geometry guards reject before any
inverse write or restoration. These cases remain failures under their original
expectation. They establish that a wheel strategy must stop on independently
verified endpoints rather than probe by scrolling again. Wheel-only arbitrary
position restoration also remains unqualified. UIA is still the stronger current
full-sweep candidate, not a promise to exclude other browser input methods.

The conversation collector and independent full-width reference pass again. At
125%, the table's native legacy render-widget client origin does not predict the
fixed authored marker placement exactly. Full-root first/last images are retained;
no fitted one-pixel correction is admitted. Chromium's pinned
`156.0.8078.80` `ScreenWin::DIPToClientRect` uses an enclosing integer rectangle,
which does not preserve a fractional painted origin. Source behavior explains
why treating that client origin as exact is unjustified; it does not establish
the missing phase. An independent bounded reference calibration remains necessary.
Raw acquisition does not consume these reference scripts or marker results.

## Installed fixed-channel native proof

Native run [42364612912](https://github.com/mikejhill/verdiclip/actions/runs/42364612912)
at `62bb93a` passes the fresh-installed-wheel channel harness. All five cases
qualify their declared lifecycle contracts: ping/finish, immediate cancellation,
after-ready cancellation, startup facade deletion, and pinned-handle parent death.
Outer cleanup is confirmed without forced termination contributing to success.
The installed-runtime capture, poison-path and schema-2 bootstrap checks pass too.
Both jobs attest the same built wheel. These are inert peer lifecycle results,
not provider, frame-transfer, GUI latency or product qualification.

Ordinary tests pass, but the Python 3.13 diff-coverage gate fails on newly added
boundary branches. Additional deterministic callback/native-seam tests are being
added while retaining the real QThread integration and native proofs. Coverage
configuration and thresholds remain unchanged. No release is authorized by the
native result alone.

The table reference still rejects its original calibration mismatch. Failure
retention now reports that root cause rather than only a secondary missing final
guard. Its dedicated outcome JSON was not included by the artifact upload glob;
the next batch adds that exact file path. Existing receipts and raw logs remain
available, but the omitted detailed outcome file is not claimed as verified.

At `007990d`, ordinary Windows CI passes again, including diff-cover at 99.875%.
The added tests cover all new helper boundary statements without changing
thresholds, exclusions or production behavior. Installed-runtime and channel
native regressions also pass. The retained table origin failure remains separate.

## Separate paint-containment control

The next independent owned-table experiment keeps the original failed origin
oracle unchanged. It captures a fixed full-root visible image, an opacity-zero
control, and exact visible restoration. Native target/root identity, offscreen
state, geometry and scroll state must remain unchanged throughout. Opacity is
the only allowed authored style change. Visibility-hidden was not selected
because it can intentionally remove the accessibility node.

Exact RGB difference support must be nonempty and entirely inside an independently
selected native rectangle. Every edge and corner must have observable support;
one-pixel inset controls must reject. The rectangle is never enlarged to fit the
observed pixels, and blended pixels are not thresholded away. Full-root visible
restoration and repeated control samples must match exactly. Any outside paint,
replacement identity, unstable state or missing edge evidence rejects.

This is one pose of an authored fixture. It tests containment of observed
panel-attributable paint, not invisible or occluded content, all scroll positions,
a universal CSS transform, or a completed scrolling capture. No product code
changes another application's opacity or visibility. Native results are pending.

### Owned opacity paint-control revision: fixed two-pixel strips

The original `3d9ad1b` table125 experiment remains **failed** under its
one-pixel-edge observability contract. Its native rectangle was
`[114,157,765,545]`; exact opacity `1 → 0 → 1` full-root differences occupied
`[115,158,765,545]`. No changed pixel was outside the native rectangle, and
restored full-root pixels and observed native identity/state were unchanged.
The left/top geometric scanlines did not change, so their original one-pixel
inset negative controls could not qualify. This is not evidence of clipping.

A separately reviewed revision, `fixed-two-pixel-strips-v2`, declares its
controls before capture. The independently selected native rectangle R is
unchanged, at least four pixels wide and high, and retains every source pixel.
Require nonempty exact RGB difference support in each fixed two-pixel boundary
strip and each 2×2 corner. Independently test all four rectangles formed by
moving exactly one edge of R inward by two pixels; each must exclude observed
support. Retain exact support bounds/count, strip counts, corner observations,
and negative-control results. Never choose R from these diagnostics.

This revision detects those **two-pixel cuts**, not every one-pixel geometric
reduction. Zero or one unchanged background pixel at an edge can pass; two
empty boundary pixels cannot. It does not prove invisible or equal-background
content. A single changed channel outside full R still fails, as do altered
A/A′ pixels, identity, native geometry/state, or ownership. No color tolerance,
adaptive band, retry, fitted origin, source crop or deadline change is allowed.
This remains one fixed owned table125 pose, not complete-scroll qualification,
a replacement of the existing matrix oracle, or product behavior. Fresh native
qualification of this revision is required; prior failure is not reclassified.

### Already-open owned WPF observation contract

The installed observation harness now separates fixture preparation from capture.
The previous cold-start-inclusive attempts at `b5f32912` and `02351554` remain
failed and unqualified; their missing telemetry is not reclassified as success.
This new contract excludes cold .NET fixture startup from capture latency and
makes no claim about opening a target application within a capture deadline.

Before launching the fixture, the outer owner fixes a ten-second preparation
limit and a twenty-five-second absolute limit including cleanup. The owned
reference executor waits for initialized observe-only telemetry and independently
pins the process creation time, HWND, foreground owner, input epoch, physical root
geometry, DPI and liveness. It publishes bounded metadata only. No MSS object,
reference pixels or provider launch is allowed in preparation. The outer reads
only bounded receipts and process/job state, then authoritatively stamps one
case start while still before the preparation deadline. A late handoff fails even
if its ready receipt was timely. The executor retains its native handles and
rechecks the same guards before any reference or provider work.

The observation case retains its fifteen-second ceiling, two-second reference
and observe limits, three-second native-call/helper-startup limits and five-second
cleanup limit. Its deadline is the earlier of case-start plus fifteen seconds and
the original absolute limit. Cleanup is also bounded by the active phase deadline;
it cannot turn forced retirement or a prior failure into success. Preparation
start, ready, case start and both deadlines are retained separately. There are no
retries, clock resets, deadline extensions or production behavior changes. Native
qualification of this revised experiment remains pending until an exact-head run.

A rejected non-singleton executor tree may receive one diagnostic request for at
most eight exact PIDs from its original owned-job snapshot. The original failure
and membership predicate remain unchanged. Inside the supervised executor,
query-limited process handles remain pinned across a later outer-owned membership
snapshot. Kernel image, creation and liveness evidence is collected only for those
PIDs. A single bounded documented Toolhelp scan copies only requested PID/parent
PID fields; unrelated process records are never retained. Parent identifiers are
snapshot metadata, not ancestry authorization. The original and later membership
observations are explicitly temporal evidence, not retroactive proof about the
original handles. No owning job handle is shared. All work and handshakes remain
within the original preparation and native-call deadlines; errors or missing
metadata cannot admit capture or replace the original singleton failure.

### Explicit owned executor profile

The next harness contract admits only an independently verified singleton or the
observed three-process profile. A singleton must be the actual executor and the
retained launch PID, with its full kernel image matching both fixed launcher and
verified base-interpreter paths. The three roles are the retained venv launcher,
the actual base-Python executor, and one console host whose expected full path is
derived from GetSystemDirectoryW plus `conhost.exe`. Observed image names are not
used to construct expectations. Both children require the observed launch parent
PID, creation no earlier than the launch lifetime, available pinned identity and
liveness, and the exact current owned-job set. Parent identifiers remain temporal
snapshot evidence, not authorization to follow arbitrary ancestors.

Baseline process handles stay retained through the case. Stable membership
checkpoints occur before reference pixels (baseline only), before provider observe
(baseline plus the production channel's kernel-attested direct helper), and after
natural helper retirement (baseline only). Startup membership is provisional and
cannot authorize provider capture or output. Unknown extra members after the
attested-helper checkpoint fail the case. No basename exemptions, launch rewrite,
new production hooks, or additional time budgets are introduced.

The executor rechecks and closes its baseline pins before exiting. This does not
claim that it observed its own handle signaled. The outer retained launcher handle
must exit naturally with code zero and the exact owned job must become empty;
forced retirement cannot qualify. All earlier singleton failures remain failed.
This explicit-profile contract requires a fresh reviewed native run before it can
qualify any image.

### Startup membership failure evidence (research only)

The first ownership research run remains failed. A rejected temporary-member
snapshot is now retained with its original timestamps, baseline, provisional
history before the predicate, proposed union, completed phases and known peer
PID. The original exception is latched before any optional diagnostic work.
Available peer and pending-phase files are retained separately as later reads.
No failed predicate is retried, exempted or reclassified.

Only a failure before the pre-observe acknowledgment may launch the fixed
stdlib-only diagnostic entry in a separate owned job. It uses the independently
verified base interpreter with `-I -S`, checks the copied harness and sidecar
hashes, and receives at most eight exact extra PIDs from the rejected snapshot.
Existing process pins and bounded Toolhelp collection are reused. Pins survive
one later outer GUI-job snapshot, then are rechecked and released. No job handle
is shared. Images, creation times and parent metadata describe later observations;
they do not establish identities at the original failure or authorize capture.
Exited, missing, late or otherwise unavailable processes remain unavailable.

The diagnostic deadline is the minimum of failure time plus three seconds, the
original case deadline and the original absolute deadline. Original GUI and
separate diagnostic call watchdogs continue. The diagnostic owner joins existing
cleanup with its tighter deadline, including on partial launch failure. No
pre-observe acknowledgment is sent after rejection. Later-phase failures retain
context only. Diagnostic completion never changes the original failure, and
partial evidence is acceptable without extending any budget or retrying.
