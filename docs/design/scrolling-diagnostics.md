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
  scrollable panel with fractional geometry.
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
controller cases now use held gestures too. The nine diagnostic jobs run only
on the named draft diagnostic branch and save exact native PNGs and numeric logs.

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
