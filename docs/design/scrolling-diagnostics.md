# Scrolling capture diagnostics after 0.14.0

This branch diagnoses reported selection failures and first-step rejection in
Windows Explorer and Microsoft Edge. It is not release qualification. The initial
commits only add passive diagnostics. Subsequent candidate fixes separately
address selection handoff, bounded perimeter registration and visible failure
feedback. Ordinary capture and the original release input tick remain unchanged.

## Passive production evidence

The existing local application log now distinguishes launch from final-publication
failure. The selection guard records expected and observed input ticks, transient
window handles, and separate input, foreground and point-hit mismatch flags.
Helper diagnostics record viewport geometry, UIA percentages/view size, expected
and measured pixel displacement, matching-band boundaries and top-mismatch counts.
The parent retains at most the last 16 KiB of helper diagnostics at retirement.

These diagnostic messages do not include window titles, URLs, document text,
screenshots or pixel contents. Exact PNGs remain test artifacts produced only by
the existing explicitly invoked hosted test harness, not production logging.

## Representative hosted observations

`scripts.scrolling_diagnostics` reuses the production controller, chooser, helper,
delivery and ordinary Repeat flow from `simple_scroll_app`. It varies the input:

- Edge prose: existing project design documentation in normal paragraphs with
  nonuniform lengths and fractional line height, not numbered colored rows.
- Edge panel: the same prose in a bordered, padded, horizontally and vertically
  scrollable panel with fractional geometry. Its 740.5-by-385.5 CSS-pixel content
  box is unchanged in the observed 976-by-608 viewport. Maximum dimensions fit
  smaller viewports, with a compact heading at heights of 500 CSS pixels or less;
  the 820-pixel inner article and horizontal overflow remain unchanged. This layout
  accommodation is not evidence of actual display-scale qualification.
- Edge sidebar: ordinary prose beside fixed navigation, to expose unsupported
  full-width registration rather than trim content to force a match.
- Explorer: an owned temporary directory containing 160 harmless text files,
  opened in installed Explorer without changing its view settings.

Explorer may create the requested folder window in a broker process rather than
the launched PID. The harness records existing ShellWindows HWNDs before launch,
then requires one new visible HWND whose canonical folder path exactly matches
the unique temporary fixture. Its live owner PID and exact folder identity are
checked before each setup lookup. Cleanup rechecks HWND, PID and folder, posts one
ordinary close to that window, and verifies the HWND disappears. It never kills
Explorer, the launcher or the desktop shell. A reused tab/window, ambiguous path,
changed identity or failed close is a diagnostic failure, not a capture verdict.

Both click and Enter are exercised for prose, panel, background Edge and Explorer.
Background cases leave Edge visible while a small owned window has focus, requiring
the production selection path to activate the exact chosen target. Native gestures
hold for 120 ms while Qt processes events between press and release. Existing
controller cases now use held gestures too. Explorer also has middle and bottom
setup variants. Middle requests 50% and must settle in the central 45–55% range;
bottom must settle at exactly 100%. These are test-setup gates, not product
tolerances. Their public UIA setup is separate from production
capture, and their exact starting state and pixels are retained for restoration
checks. Bottom starts now attempt the same guarded movement to the top as other
starting positions; any unexpected provider metric change still rejects without
a speculative retry. All starting positions require successful full capture and
restoration before release. The thirteen capture diagnostics and separate
`explorer-inverse-restore` experiment run only on the named draft diagnostic
branch and save exact native PNGs and numeric logs. The inverse experiment uses
one native-range-derived UIA restoration request against an owned fixture; it
does not exercise or qualify production restoration.

The separate `explorer-step-geometry` experiment runs only in the verified 200%
session. On a fresh owned Explorer fixture it records two ordinary half-view
requests, one endpoint request, and one original-native-position restoration
request. Raw retained-item geometry and exact PNG overlap measure the two local
displacements independently of UIA percentage predictions. Endpoint items are
observed separately, without claiming continuity across the direct jump. Any
uncertain write, changed eligibility or expired budget stops further writes,
including restoration. Success requires the original native position and every
non-vertical-rail pixel, including Header, horizontal rail and corner, to be
restored exactly. The experiment never produces a stitched image or qualifies
production capture, and it does not change the product's extent tolerance.

A rejection fails its diagnostic job and is evidence to investigate, not a
qualified success. Representative sequences retain all frames within the existing
80-frame/40-million-pixel limits. An offline oracle maps each original frame to
absolute global rows using UIA percentages and checks every complete output row
against acquired pixels, without calling the stitcher or reusing its seams.
Wrong dimensions, changed/missing output rows and incomplete end coverage reject
the case. Provider geometry can still be imprecise, and matching rows do not prove
hidden content semantics or repair repeated scrollbar thumbs; inspect the PNGs.
The original thirteen native qualification cases and ordinary regressions still
run separately. Actual shell tray-icon entry, physical pointer jitter, and the
user's particular app geometry still need discriminating evidence.

## Observed candidate results

Exact source `be4c38300c384aa254c56e1e005ce807a60d117a` ran in
[native run 28](https://github.com/mikejhill/verdiclip/actions/runs/42470673406).
All thirteen original native cases and nineteen ordinary native checks passed,
including failure-feedback nonactivation. General CI and both CodeQL analyses
also passed. The diagnostic matrix is not green:

- Prose, bordered panel and deliberately background-selected Edge passed both
  click and Enter. Prose/background captured nine frames and 3,012 output rows;
  panel captured fourteen frames and 3,036 rows. The independent absolute-row
  oracle checked every full-width output row. A separate decoded-pixel audit of
  the panel-click, prose-enter and background-click artifacts confirmed those
  results and original first/last boundary rows.
- Background cases recorded exactly one native activation of the selected HWND.
  Prose/background restored identical pixels and exact H/V/view-size state.
  Panel restored that exact scroll state and all interior pixels, with 4,740
  changed pixels confined to its known four-pixel outer focus perimeter.
- Panel output still contains the outline color transition and repeated native
  scrollbar thumbs. Text clipped horizontally in the original viewport remains
  clipped. These results cover full visible width, not hidden document width.
- Explorer click and Enter still reject after the first step: measured movement
  is 254 pixels while the full-height UIA calculation predicts 265.832 pixels.
  Both restore scroll state and close only the verified fixture window. A
  complete 107-node numeric probe identifies a visible standard 807-by-25 Header
  and adjacent 17-pixel ScrollBar inside the 824-by-532 List. This is evidence for
  a separate header/body model, not permission to guess a crop.
- The fixed-sidebar page still rejects the top consistency check at
  1,631 / 31,232 changed pixels (5.22%). Its failure is retained, not converted to
  a successful negative qualification or fixed by increasing the threshold.

All reported target windows had `GetDpiForWindow == 96`. This run did not measure
independent monitor scaling and does not qualify 200% or fractional scaling.
Actual 200% display setup and readback, complete Explorer content, and subsequent
source changes require their own exact-head native evidence before release.
