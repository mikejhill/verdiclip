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

## Initial hosted result and diagnostic correction

PR107 at `5f02402a` stopped on the first arrow/normal case during setup,
with cleanup confirmed. No owner admission or restoration was qualified. The
initial receipt did not retain the exact setup predicate; private child output
was not archived, so the cause cannot be reconstructed. That uncertainty is
preserved, not attributed to a cursor or foreground cause.

The follow-up adds a finite allowlist of parent/child operation stages and error
categories. It emits neither exception messages nor native identities. The first
failure survives later cleanup failures. Separate ready, process identity,
foreground, physical hit, thread cursor, class cursor and global cursor checks
retain the original admission conditions. Child snapshot operations identify
which UI-thread API read failed. The cleanup predicate is named outside finally
to avoid the known scanner lambda false positive; no cleanup behavior changes.

## Direct-child runtime admission

The next hosted head stopped at the combined peer-identity check. This does not
identify which conjunct failed. CPython's Windows venv redirector launches another
process; this is a plausible explanation, not an established cause. The isolated
harness now follows the official Windows multiprocessing strategy: launch the
verified base interpreter directly with a copied child environment whose
`__PYVENV_LAUNCHER__` explicitly identifies the current venv executable. There is
no fallback interpreter and no parent-environment mutation.

Before constructing QApplication, a fixture HWND, broker or cursor surface, each
child sends private runtime attestation. The parent requires its exact Popen PID,
actual interpreter image, venv/base paths, interpreter version/bitness and Qt/
PySide versions and package paths to match independently measured parent values.
Only then does the parent authorize `run`. All setup work, including this
handshake and readiness, shares the original five-second deadline. PID and HWND
ownership predicates remain strict and have separate fixed failure labels.

A runtime-only hosted control runs before the first native case and confirms the
direct-child PID and runtime identity without constructing a GUI. Public receipts
contain only its verification/cleanup booleans and fixed failure labels, never
runtime paths or IDs. Rejection stops native cases, retains exact-child cleanup
ownership and is not evidence of cursor support. No cursor behavior or restoration
oracle changes follow from this launcher correction.

Sources: [CPython Windows venv launcher](https://github.com/python/cpython/blob/3.13/PC/venvlauncher.c)
and [Windows multiprocessing launcher](https://github.com/python/cpython/blob/3.13/Lib/multiprocessing/popen_spawn_win32.py).
