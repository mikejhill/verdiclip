# Scrolling capture diagnostics after 0.14.0

This branch diagnoses reported selection failures and first-step rejection in
Windows Explorer and Microsoft Edge. It is not a capture fix or release candidate.
No selection baseline, UIA write, matching threshold, input guard or ordinary
capture behavior is relaxed.

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

Both click and Enter are exercised for prose, panel and Explorer. Native gestures
hold for 120 ms while Qt processes events between press and release. Existing
instantaneous-input qualification remains unchanged. The seven new jobs run only
on the named draft diagnostic branch and save exact native PNGs and numeric logs.

A rejection fails its diagnostic job and is evidence to investigate, not a
qualified success. Even an accepted prose/Explorer image proves only capture,
restoration and delivery; inspect the PNGs before claiming content completeness.
The original thirteen native qualification cases and ordinary regressions still
run separately. Actual shell tray-icon entry, physical pointer jitter, and the
user's particular app geometry still need discriminating evidence.
