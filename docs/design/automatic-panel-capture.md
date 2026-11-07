# One-click automatic panel capture

Design proposal for the isolated release after **0.13.2**. The implementation
base is `b8254f2a9c6dab9d50bd6b174a5651b2aff92201`. This is not a restoration of
the removed 0.13.0 scrolling workflow. Normal capture and its frozen-menu/input
ownership behavior remain a separate path.

## Interaction

1. Choose **Capture scrolling panel** from the tray or its configurable hotkey
   (default `Ctrl+Alt+PrintScreen`). VerdiClip freezes the desktop and presents a
   point-only selector: **Click inside the panel to capture all of it. Esc or other input cancels.**
   The ordinary native cursor remains in use. There is no configuration/review
   dialog, drag-to-define viewport, scroll-distance field, or manual stitching.
2. Click once inside the desired content. The selection click is consumed by
   VerdiClip, not replayed into the application. After every selector has closed,
   identify the smallest supported scrolling ancestor containing that point.
   Nested panels therefore select the inner panel; clicking outer content selects
   the outer one. A click on a scrollbar or ambiguous unsupported target fails
   without scrolling a different area.
3. Capture automatically from verified top to verified bottom. A nonactivating
   tray icon/tooltip indicates capture progress without opening a balloon that
   could overlap the viewport. The selector already explained cancellation.
   No foreground activation, pointer movement, keyboard shortcut, wheel event,
   foreign hook, injected module, or script in another application's process is
   required. The user need not interact again.
4. On success, restore the original scroll position while ownership remains
   valid, then deliver exactly one normal `Capture` through existing tabs,
   clipboard, and/or file settings. Restoration failure is reported separately;
   it cannot convert an incomplete stitch into a successful capture.
5. Escape requests immediate cancellation. Any subsequent physical keyboard or pointer input,
   foreground change, resize, target closure, or obscuring window cancels/fails
   safely. After detected external input, VerdiClip never requests restoration or
   another scroll; an already in-flight provider call cannot be undone. A notification says if the panel was left at a different position.

Repeated activation while a capture is running neither creates another worker
nor steals focus. Normal capture and scrolling are mutually exclusive. Failure
returns the application to an immediately usable state, with no empty editor tab
or clipboard/file change.

## Scope and honest limits

The first release supports visible, finite, already-loaded static vertical Windows
panels whose qualified accessibility provider exposes a coherent UI Automation
`ScrollPattern` with readable position/view size, working `SetScrollPercent`, and
pixel-continuous scroll metrics verified against actual pixels. Logical/item-unit
providers without that agreement are unsupported. It captures the
visible width; it does not scroll horizontally. The entire viewport must be
visible on one monitor. The selected panel's view size and bounds must stay
constant throughout capture. Accessibility permission/integrity failures,
unsupported custom-painted controls, uncertain bounds, stationary headers within
the content, changing/animated content, and ambiguous/repeating/blank seams fail
with a useful explanation, not a guessed or silently partial image.

A composite records the panel during the capture interval, not one impossible
instantaneous snapshot of offscreen content. Observable layout/content drift is
rejected. Current endpoint evidence is required. Detected extent growth, loading, or dynamic
content fails without output. A bounded observation cannot prove that a remote
feed will never append later; such future content is outside the supported
already-loaded static-content envelope. Arbitrary Windows
applications are not promised; release notes identify the provider families
actually qualified. Browser support requires an independent real browser test.

## Technique decision

| Technique | Useful property | Decision |
| --- | --- | --- |
| UIA element under point, bounded ancestor walk, ScrollPattern | Panel identity, nested selection, top/bottom evidence; does not take native input focus | Primary and only initial control path |
| Wheel/keyboard SendInput | Broad practical reach | Not initial fallback: pointer/focus ambiguity and no reliable endpoint or restoration proof |
| WM_VSCROLL/GetScrollInfo | Useful for some native controls | Not initial fallback: application-specific units, custom controls, and message semantics |
| Browser DOM/devtools/script injection | Could expose complete DOM/layout | Excluded; no injecting code or special browser permission requirement |
| Image-only scrolling/stitching | Can align actual pixels | Alignment verifier only; repeated frames alone never establish completion |

UIA's percent and view size guide small step requests. They are **not treated as
pixel displacement**. Only a uniquely verified pixel overlap determines the
number of new output rows. No percentage-to-pixel gap filling or scale correction
is allowed.

## Ownership and architecture

- A separate scroll coordinator, selector, frame stitcher, and native adapter.
  Existing `CaptureService` frame preparation, menus, region/window/full-screen/
  repeat, cursor behavior, and tab presentation are not reimplemented.
- One new owned child process per scrolling session, launched directly as the
  application/Python module. No PowerShell, runtime compiler, broker, persistent
  process, background readiness warm-up, or broad compatibility layer. Native UIA is called
  only in the helper's windowless MTA. A small explicit ctypes COM adapter uses
  documented UIAutomationCore interfaces and is tested independently.
- The parent uses asynchronous `QProcess` signals and a bounded line-delimited
  protocol. Exactly one command is outstanding. Request IDs prevent stale
  replies from admitting frames. Per-command watchdogs kill a stuck helper;
  parent exit/shutdown/cancel also ends its exact owned process lifetime. Windows
  venv executables can be launchers, so killing only the QProcess PID is not
  sufficient. A verified direct base interpreter with an explicit, isolated venv
  package path or a kill-on-close owned job must cover the real producer, including
  before-ready failure. No detached relay launch. Native evidence records both
  launcher and producer identities and proves no survivor. No GUI-thread UIA or waits.
- The selector converts screen-local Qt logical click coordinates through existing
  `ScreenGeometry.to_physical` exactly once, preserving negative monitor origins.
  UIA FromPoint and native RECT are already physical and are never rescaled. The
  helper establishes/verifies per-monitor DPI awareness before geometry/MSS/UIA;
  virtualized native bounds or display-layout drift are failures.
- The helper owns its own MSS handle and bounded image buffers. It reads native
  pixels, performs UIA calls, and writes frames to a parent-created private
  temporary session directory. Parent validates response sizes, paths, identities,
  and image dimensions. Cleanup occurs on success, failure, timeout, or cancel.
  No screenshot, panel text, or title is written to diagnostic logs by default.
- Before and after every operation/frame: check the session input epoch (`GetLastInputInfo`),
  foreground/target HWND and PID identity, bounds, screen layout, visibility and
  occlusion. UIA target identity is retained in-process; no fuzzy title matching.
  Only the chosen target's ancestor chain is traversed (maximum 32 levels).
  A cold browser accessibility tree may expose only shell panes. After ordinary
  no-pattern chain exhaustion, one bounded one-second read-only settling
  observation may repeat discovery with the same client and input epoch. No
  provider-error retries, polling extensions or writes before target validation.
  Scrollbar discovery is bounded to the selected subtree (maximum 64 nodes).
- Temporarily register unmodified Escape with the existing native hotkey owner.
  Registration must succeed before the first scroll; release it on every exit.
  No global hook is installed. Other input is observed, not swallowed. Interference
  is latched irreversibly; compare native 32-bit ticks for equality, not ordering. The last
  native input tick after selector dismissal defines the immutable ownership
  epoch. If it changes, stop before issuing the next command and do not restore.
- The initiating click/release is settled before establishing the input epoch.
  UIA scroll calls do not generate native input or require focus. Native platform
  guards and the parent watchdog remain separate from provider RPC. An in-flight
  call may have an uncertain outcome when cancelled/timed out; never retry it or
  restore blindly. Kill the helper and report the uncertain position.
- The controller includes scrolling in duplicate-request, shutdown, exit, and
  automatic-update-idle checks. Completed images enter `_on_captured` once.
  The controller records the last successful capture kind. If it was scrolling,
  Repeat opens the one-click scrolling selector again; otherwise Repeat delegates
  unchanged to the ordinary capture service and its retained region/window/screen
  history. Failed/cancelled scrolling does not change that last-successful-kind
  record. Never repeat just the old viewport as though it were complete content.

## Viewport and frame verification

A supported target supplies a finite, nonempty physical UIA rectangle contained
in one display. Accessible visible scrollbar rectangles may be removed only
when they form a contiguous edge strip of that viewport. The remaining rectangle
must contain the user's click. No heuristic crop removes unidentified sticky
headers, borders, or content. Partially offscreen and occluded panels are rejected
before scrolling and rechecked before frames; `FrozenScreen.crop` clipping is
not used to excuse missing pixels.

Initial state saves position, view size, horizontal position, target identity,
viewport, input epoch, and foreground owner. Seek vertical percent 0 while explicitly retaining the acknowledged horizontal
percentage when that axis is scrollable (`NoScroll` only for a non-scrollable
horizontal axis); read back top and wait for stable pixels. Chromium currently
fails to honor `NoScroll` for a scrollable axis. Readback must still prove the
horizontal position unchanged, without corrective retries. A valid frame
requires three matching samples over a bounded quiet interval, with unchanged
provider state. A provider at top alone is insufficient.

For each forward step, request at most half a nominal viewport based on view size
and the current percentage. The unique pixel displacement must also agree within
qualified raster-rounding tolerance with the displacement implied by the current
percentage change and stable view size. This is a rejection check, never a source
of invented output rows. A provider that jumps in noncontinuous logical/item
units or unexpectedly advances a whole repeated period is unsupported. Failure to
obtain an unambiguous overlap stops capture. There is no gap interpolation.

The stitcher compares complete content-width scanlines in the overlap interior.
Native WPF evidence shows viewport-edge glyph clipping can alter the first
scanline by up to 3/255 even after an exact integral scroll. Accordingly, exactly
one scanline at each end of the overlap is excluded from alignment evidence,
not from output. Every interior channel must match exactly, with no color
threshold or percentage-guided disambiguation. The verified interior must be at
least one quarter of the viewport (and at least 24 rows), and exactly one
candidate displacement may satisfy it. No-progress and ambiguous/repeating
content fail. A one-pixel edge change cannot always be distinguished from
clipping; this explicit limitation replaces the prior exact-boundary claim.

Splice at the midpoint of verified overlap: retain the existing composite before
the seam and use the new frame after it. This avoids both viewport-clipped edges
at internal seams. Output height must increase by exactly the measured physical
displacement; no interpolation, missing rows or duplicate rows. The initial top
and final bottom edges are retained from their endpoint frames. Keep the original
exact-overlap diagnostic alongside boundary-aware results in qualification.

Completion needs all of:

- verified top frame;
- strictly increasing provider position and a valid unique overlap for every
  accepted forward frame;
- stable view size, viewport, identity, horizontal position, input epoch, and
  content overlap throughout;
- reported vertical percent 100, followed by another bounded request to 100,
  unchanged endpoint state and stable identical pixels;
- final output extent agrees with the extent implied by the stable view size
  for the qualified pixel-continuous provider envelope (rejection only, never
  synthesize or crop to force agreement);
- no limit, cancellation, timeout, or unverified frame in the capture chain.

Identical pixels alone do not indicate bottom. Repeating/blank content is
explicitly rejected where displacement is ambiguous. Extent growth/shrink is
rejected even if percent currently reports 100. A replay/check of the initial
frame after return to top can detect content changes that are no longer onscreen;
this is required before success, followed by restoration to the saved position.
It is still not an assertion that arbitrary undetectable remote content never
changed between samples.

## Bounds, termination, and restoration

Initial limits: 90 seconds total, 128 accepted frames, 64 million output pixels,
32,768 output rows, 16 million pixels per viewport, 3 seconds per native command,
2 seconds to settle a frame. Limits are internal safety
ceilings, not user configuration or arbitrary latency performance claims. They
are checked before allocating/appending or issuing another scroll. Image size
limits may need lowering after measured memory qualification, never raising to
hide a failure. Matching works on bounded frame bytes rather than GUI-thread
pixel-by-pixel loops.

Success and ordinary provider/stitch errors restore only if identity, geometry,
input, extent, and foreground ownership still match, the current percent equals
the last acknowledged state (no programmatic drift), and no native operation has
an uncertain result. Restore horizontal state unchanged and vertical percent to
the saved value, then verify readback and stable pixels. On input/focus loss,
closure, timeout, or shutdown, stop without further requested scrolls. An already
in-flight call may have an uncertain outcome; never promise to undo it. Notification
is truthful about restoration. No automatic restart/retry and no partial output.

## Bounded MVP acceptance (release blockers)

1. Real one-click selector journey, tray + registered hotkey, no configuration
   dialog, no pointer relocation. Esc before click and in progress returns to idle.
2. A separate owned WPF fixture with a native ScrollPattern panel, starting in the
   middle and at bottom. The resulting image includes every known colored/text
   row exactly once, including first and final clipped rows. Original position
   is restored. Sibling pane and horizontal position remain unchanged.
3. Nested inner/outer panels: click resolves the intended smallest ancestor;
   selected inner completes while outer and siblings remain unchanged.
4. Real installed Edge on a local static page with nested overflow, through its
   real accessibility provider. If unsupported, record that limit and evaluate
   whether provider-family breadth is adequate before release. Do not present
   single-WPF success as general browser compatibility.
5. Negative native fixtures: animated pixels, extent growth/infinite simulation,
   delayed append at endpoint during qualification, view-size drift, ambiguous
   repeated rows, unsupported provider, resize/close/occlusion, external
   input/foreground change, Escape, hung provider/helper. Each has one terminal
   result, no delivery, no later scroll, bounded cleanup, and next capture works.
6. An adversarial periodic/logical-unit-jump fixture must not falsely succeed.
   Small percent requests and local overlap uniqueness are not universal proof
   against arbitrary providers jumping entire repeated periods. Qualify a bounded
   provider envelope; reject incoherent movements. An unconditional second sweep
   is excluded: it doubles exposure/latency and does not prove away periodic aliases.
7. Deterministic coordinate cases at 100/125/150/200%, negative origins, adjacent
   monitors/gaps, and layout changes. Available native CI scaling is labeled
   separately; no user display-setting change.
8. Deterministic tests for every state transition, unique/no/ambiguous overlaps,
   clipped final frame, bounds and malformed protocol, every cleanup path, process
   exit and timeout, failed restore, duplicate requests, and output admission.
9. Existing full gate, coverage/diff coverage, tabs CI, native capture smoke,
   36-case native lifecycle matrix, and freeze-first comparisons remain unchanged
   and pass against the exact candidate head. Native artifacts record source
   digest, runtime, input provenance, output pixels, and first failures. No
   physical testing on the user's laptop/X1 and no blind reruns.
10. Independent design/source review and native QA before publication. Native
   helper startup/first probe is tested early, before building a large harness.

## Sources

- [UIA threading guidance](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)
- [Finding UIA elements; avoid desktop descendant enumeration](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/obtaining-ui-automation-elements)
- [ScrollPattern SetScrollPercent](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationscrollpattern-setscrollpercent)
- [GetLastInputInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo)
- [RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)
