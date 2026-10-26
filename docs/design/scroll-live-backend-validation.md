# Scrolling acquisition component checkpoint

Historical checkpoint for `d68a6bd`. For the later private-IPC/geometry fixes,
portable CI boundary and supplemental native findings, see
[capture-validation-matrix.md](capture-validation-matrix.md).

This is a component checkpoint for draft PR #61, not a feature-ready release.
The published 0.12.1 installation is unchanged. Merge remains subject to Mike's
local verification of the completed combined preview.

## Functional boundary

`ScrollTargetPanel.confirmed(ScrollTarget)` feeds
`ScrollCaptureBinding.start_confirmed(target)`. The binding verifies the target,
samples settled frames, requests one movement at a time, stitches a confirmed
prefix, and emits `review_ready(session, note)` after finishing its backend.
The controller constructs the existing `ScrollReviewPanel` and uses the agreed
`ReviewedCapture` delivery/ID acknowledgement API. This component adds no shell
controller or tab changes.

`UiaFrameBackend(reference, adapter, options=NativeBackendOptions(...))` implements
that backend with a warm, application-owned MTA helper lease and private bounded
PNG handoff. Helper replies are bounded and correlated. Cancellation invalidates
work before killing only its own child. Private frame cleanup follows confirmed
child termination. Stop can retain the verified prefix and best-effort restore;
external input or programmatic position/extent drift suppresses restoration.

The backend is **unavailable by default**. Both qualified preservation capability
and a latched unchanged-input predicate are required. The production native input
broker, service/overlay integration, convenient scrollbar targeting entry point,
and the full native preservation matrix remain unfinished. Existing ordinary
capture still uses the released implementation until that integration is ready.
No claim is made that ordinary capture currently meets the corrected no-dismissal
criterion.

## Validation of this component tree

- Clean validation worktree: 1,402 passed, 15 expected skips, 95.09% coverage.
  Full offscreen run completed in 41.51 seconds. The initial sandboxed run denied
  localhost and disposable HKCU test-key access; the permitted rerun passed.
- Formatting, Ruff and strict ty checks passed on the clean tree.
- 87 focused tests passed, including fictional pixel/JSON peer integration through
  the real production binding, backend and PNG decoder; stop/cancel; malformed
  pixels; process lifetime; cleanup failure; external scroll drift; and pointer
  replay. No native desktop APIs run in the fictional peer.
- Three targeted mutants were killed: accepting SAMPLE position drift, ignoring
  extent drift before native movement, and replacing the tracked release endpoint
  with its origin. Original source bytes were restored after each mutation.
- Independent Codex review found three P2 issues, all fixed and re-reviewed with
  no remaining actionable findings: SAMPLE position drift ownership, cleanup
  callback exception containment, and pre-movement extent validation.
- Installed Windows PowerShell 5.1.26100.9711 parsed the helper and compiled its
  UIA assemblies/C# declarations. The compile-only check invokes neither native
  declarations nor the helper's provider/pixel body. A separate test executes
  only the extracted pure scroll-ownership predicate.

## Native evidence and remaining qualification

A bounded output-only, nonactivating overlay/input prototype completed one
synthetic CUA drag with a separate process's Win32 TrackPopupMenu still open.
Its report recorded `native_menu_returned=false`, `hooks_removed=true`, and no
owned processes remained after cleanup. The menu returned only during fixture
cleanup. This is feasibility evidence, not production UIA backend qualification.

The recorded events were MOVE(259,194), DOWN(262,168), MOVE(369,244),
UP(262,168). Both button packets retained the frozen OS cursor. The pure
`PointerTracker` therefore takes no button coordinates and uses tracked moves,
yielding Rect(259,194,110,50). Replay tests also cover relative movement math,
negative desktop origins, synthetic DPI scales, bounded coordinates, unmatched
packets and terminal cancellation. Physical mouse/trackpad behavior remains
unqualified; replay does not establish it.

Desktop input was handed to the Tabs worker after that probe. No further native
input is started without a new coordination handoff. Remaining real Windows
checks include production broker integration, owned modal/nested/tray states,
foreign menus, Escape/drag/repeated requests, actual helper target/scroll/pixel
behavior, and restoration/interference. No security or input-target guard bypass
is permitted. Unsupported cases must remain visibly unavailable.

## Local evidence

Evidence lives under the task root, outside packaged application sources:

- `scroll-functional-aggregate-permitted.log`
- `scroll-live-targeted-mutations/results.json` and individual failure logs
- `native-output-only-batched-drag/` (synthetic native feasibility fixture)
- `scroll-functional-reviewed.patch` (tested component diff from 9a7e7b4)

The unrelated Qt-only input lease and capture service experiment are deliberately
excluded from this component commit and validation tree.
