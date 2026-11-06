# Isolated cursor feasibility, not a product repair

This experiment is based on PR94's unqualified CursorVisibilityLease candidate.
It does not change PR91, the candidate implementation, or ordinary CI. It runs
only on a disposable GitHub-hosted Windows runner, on draft branch
`diagnostic/cursor-feasibility`. Never run it on a user's desktop.

## Independent presentation prerequisite

`tests/capture/cursor_oracle.cpp` uses the Windows SDK Desktop Duplication API.
It owns a flat-background window, prepares a bounded balanced cursor baseline,
and verifies both the desktop surface and the independently composed pointer
plane. A changing fixture marker verifies fresh desktop presentation. The entire
sampled region must belong to the fixture, and the pointer must stay at its
owned anchor. Images and shape bytes exist only transiently; no image, coordinates,
handles, native IDs, titles or provider messages are emitted or retained.

The limited oracle supports only bounded COLOR pointer shapes with at least one
fully opaque pixel contrasting with the known background. Other pointer formats,
ambiguous/baked pointer pixels, missing pointer metadata, ownership changes,
unsupported surfaces, inaccessible duplication and deadline failures are
**unsupported**, never successful suppression. Desktop bitmap equality alone
is not an oracle: hardware cursors can be composed separately. GetCursorInfo
is used only inside the production candidate's eligibility check, not to decide
presentation.

Oracle bounds:48 commands,100s lifetime,15s idle,2.4s per observation,50ms frame
waits and bounded nonblocking GPU map. Surface allocation is bounded by dimensions
and pixel count. It balances its own ShowCursor calls and attempts only its own
saved cursor position/shape/window cleanup. Its explicit quit receipt reports
cleanup separately from product/global state.

Before each mutation or forced termination, the supervisor arms a QPC boundary.
The following observation requires an explicit pointer update at or after that
boundary as well as fresh surface evidence. A cached state, including an unchanged
hide after termination or another hide request, is inconclusive and stops the
experiment as unsupported. A fresh desktop marker alone cannot synchronize the
pointer plane. Baseline and deliberate no-op checks are unarmed snapshots.

## Experiments and interpretation

A separate Python supervisor uses file-backed stdout rather than pipe-reader
threads, a finite command budget and bounded child termination. No UIA preflight,
real hotkey, unrelated input ownership, process attachment or screenshot output
is involved. The actual production CursorVisibilityLease class runs in an owned
child, not a fake replacement. This is lease-only evidence, not full broker,
virtual-crosshair, physical-input or end-to-end capture qualification.

1. Require independently visible baseline. An intentional no-op must remain
   visible, establishing that the oracle rejects a missing suppression action.
2. Observe ordinary acquisition, suppression and explicit product closure.
3. With a second owned Magnification client, observe a pre-existing hide before
   acquisition and whether product closure overrides that client's hide.
4. Repeat with the second client's hide issued while the product lease is active.
5. Force-terminate the exact owned product child. Observe presentation **after
   confirmed process termination and before creating any supervisor rescue**.

After each case, stop owned actors and explicitly restore the known visible
fixture baseline using a separate owned rescue client. Rescue visibility is
recorded separately and cannot turn failed product restoration, interference or
crash recovery into a pass. The final receipt always says release_qualified=false
and retains the known global uncounted API incompatibility. A completed diagnostic
means only that observations were collected; it does not qualify PR94 for merge.
An unsupported prerequisite or phase exits nonzero and stops further cases.

The [Desktop Duplication documentation](https://learn.microsoft.com/windows/win32/direct3ddxgi/desktop-dup-api)
explains separate pointer composition. [MagShowSystemCursor](https://learn.microsoft.com/windows/win32/api/magnification/nf-magnification-magshowsystemcursor)
has no ownership token or prior-state getter. An oracle pass cannot remove that
underlying preservation incompatibility.

## Validation limits

Portable tests exercise the coordinator's negative control, unsupported gate,
competing-client interference and crash-before-rescue ordering using fake ports.
They do not prove native presentation. Windows compilation and fresh hosted
runtime validation remain required after independent source review. No existing
failed CI is rerun unchanged, and no real desktop is used for this implementation.
