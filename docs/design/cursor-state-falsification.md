# Disposable owned cursor-state falsification

This is a test-only successor to PR105's admission experiment. It does not modify
production capture, claim rendered cursor hiding, enable popup support, or qualify
a release. Run only in the branch-restricted, same-repository draft workflow on a
disposable hosted Windows runner. There is no user-desktop route.

## Contract and missing guarantee

[SetCursor](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setcursor)
affects a shared cursor. Its documented client-area ownership rule supports a
scoped call from the actual owned window's
[WM_SETCURSOR](https://learn.microsoft.com/en-us/windows/win32/menurc/wm-setcursor)
handler. It does not establish universal isolation from another client's state.
Neither that contract nor
[DestroyWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-destroywindow)
guarantees that destroying the window restores the underlying policy at a
stationary pointer. This experiment attempts to falsify that assumption.

The only explicit cursor mutation is `SetCursor(NULL)` in the owned natural
handler. The window, wParam, HTCLIENT result, physical point, active broker,
foreign GUI recipients, process identities and integrity must all agree first.
Menu/capture and unsupported cursor baselines refuse. The handler returns TRUE;
it never forwards to a Qt cursor handler. There is no saved-handle restoration,
ShowCursor, class-cursor replacement, foreign queue attachment, injected input,
synthetic WM_SETCURSOR, pointer movement or borrowed capture.

## Independent observations

Two sequential fixture clients use distinct standard class cursors. Fixture
creation/foreground setup precedes each baseline. Each client remains in its own
process and samples its own thread cursor and class policy on its UI thread.
Private stdin/stdout messages carry nonce/sequence-correlated snapshots and
opaque identities; these are never published as artifacts. The observer verifies
each HWND against the exact Popen-owned PID. Current global cursor information is
read independently. GetCursorInfo is API state evidence, not rendered pixels.

While the owner has input, only the intended NULL global cursor state can differ.
Foreign GUI state, physical position and independent client policy remain exact.
The first post-DestroyWindow observation is taken before broker drain. The parent
also samples after receiving that boundary, checks the independent client, and
only then permits broker drain. Any first mismatch permanently fails; no polling
for eventual baseline equality is allowed. Fixed later samples over one second
may discover delayed interference but cannot erase an earlier failure.

Both clients run normal/cancel/repeat cases before any crash case. Crash cases
terminate only the exact disposable owner process, which owns the HWND and
broker. They do not rely on its finally block or a supervisor cursor restoration.
The parent's first observation after process exit must already match the original
baseline, followed by independent client-policy and fixed later checks. Any case
failure stops all subsequent safety cases. A complete experiment with a refused
or failed case is not successful cursor qualification.

## Bounds, cleanup and limits

There are at most eight cases, 64 private messages per pipe reader and 64 natural
cursor messages per policy. Setup receipts have a five-second bound; ordinary
snapshot receipts have a one-second delivery bound. Broker startup/retirement
retain 2.5/1.5 second bounds. Each owned phase lasts one second (50 ms for cancel).
Child cleanup allows two seconds then kills only its exact Popen child and waits
two more seconds; forced cleanup remains a failure. The workflow has a three-minute
experiment step and five-minute job bound.

Failed HWND retirement retains native callback ownership until that owned process
exits. Cleanup never repairs cursor state. A failed observation may leave the
cursor state unsuitable in this disposable VM; terminating the job is acceptable,
but adding a global restore is not. No subsequent case may hide that failure.

Passing these finite samples would establish only the recorded cases. It cannot
prove absence of unsampled transients, eliminate the guard-to-call race, establish
mixed-DPI/physical-input acceptance, or replace rendered presentation evidence.
The popup/capture limitation from Stage A remains a product-design blocker.
