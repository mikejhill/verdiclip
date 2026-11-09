# Native scrolling qualification

The hosted scripts exercise the actual controller, chooser, helper and ordinary
capture recovery. Only test fixtures produce PNGs and numeric artifacts.
Production logs exclude titles, URLs, document text and pixel contents; retained
helper diagnostics are bounded to 16 KiB.

## Required coverage

- Ordinary Edge/table, nested panel and Notepad captures, selected-window
  click/Enter, changed input/focus, covered target, cancel and application
  tray/hotkey/Escape paths. Notepad is a smoke, not a full-content oracle.
- Existing real freeze, popup/modal, tab and failure-feedback focus regressions.
- Traced 160-file Explorer top/middle/bottom click/Enter at actual 125% and 200%,
  with complete raw-frame/full-row/native-item/endpoint proof and exact permitted
  restoration. Header, horizontal rail and corner remain part of the proof.
- Mandatory tiny ordinary-helper completion against a fresh audited same-window
  reference and complete visible file inventory. A resource failure cannot
  replace this positive completion.
- No-horizontal-bar List coverage at 96/125/200%, with verified H=-1, actual
  List begin/finish counts and complete output/restoration checks. A setup
  failure is a gap, not a product pass; actual 200% remains prospective.
- Edge prose/panel/background and focused-panel controls, with actual DPI and
  independent full-row checks. Fixture-only authored-content restoration is
  limited to the explicitly source-hashed fixtures. Nested output retains its
  existing exact criterion; no new pixel mask or exception is authorized.

Tiny fully traced Explorer is a stress diagnostic. A failed capture remains a
failure with its original error and artifacts; elapsed observer time is never
subtracted. Supported representative captures and ordinary tiny completion are
separate proofs. The fixed-sidebar fixture remains unsupported; until its exact
provider-negative criterion is independently reviewed, its capture failure is
not a green negative test. No other error becomes acceptable by implication.

Every release gate needs exact-source checkout, display setup/readback, target
DPI, owned cleanup and always-uploaded artifacts. Final merge/main/release source
requires fresh qualification; historical results cannot qualify a pruned tree.
The native workflow routes these gates to main and release candidates. Once the
workflow is on the default branch, optional tiny stress is available by manual
dispatch on main. That invocation runs Edge200 controls and tiny stress only,
not full release qualification. Ordinary push/PR qualification does not depend
on manual dispatch.

## Product and oracle boundaries

The selected List/Header/native-rail route uses raw retained-item identities,
unique pixel overlap and both final extent bounds. Restoration authority requires
proven terminal state; changed ownership/input/geometry or uncertain writes stop
further writes. Failure never publishes a partial image, retries restoration or
falls back to an old percentage. The 33/35/37-second traversal/transaction/owner
bounds remain unchanged.

Accepted-frame oracles retain complete output width, original boundary rows,
Header/footer, endpoint extent and exact native state. The ordinary tiny helper
comparison excludes only the measured vertical rail and requires the full footer
and all non-rail pixels. It is not chooser coverage or tiny intermediate-frame
provenance. Positive horizontal offsets, arbitrary providers/layouts and universal
content-mutation detection are not qualified. Capturing visible width does not
capture text clipped horizontally or repair repeated scrollbar raster.

## Historical evidence, not cleanup qualification

[Native52](https://github.com/mikejhill/verdiclip/actions/runs/42527207921)
ran source `6eedf75a257a7790761e3455ec7dddbbb8eb96ed` before this cleanup.
All twelve H0 representative cases passed independent full-row and restoration
checks. The ordinary tiny helper completed 52 frames in 31.7690917 seconds with
all 160 names and exact permitted restoration. Fully traced tiny top-click
failed at the traversal cutoff after 49 frames and did not restore the original
interior; it remains a failure. Five 125% no-H cases passed, one failed Settings
launch, and all six 200% no-H cases failed fixture Show lookup before capture.

The nested Edge125 raster was exact in native52; its five changed corners in
native51 remain a historical failure, not a reason to relax today's criterion.
Original runs and immutable artifacts are retained outside this release tree.
Closed inverse, geometry, exact-region and phase-profiling commands were removed;
the raw rail reader, native identity validation and all live output/safety oracles
remain. No native execution of this cleanup is claimed here.
