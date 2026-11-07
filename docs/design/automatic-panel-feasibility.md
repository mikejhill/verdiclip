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
