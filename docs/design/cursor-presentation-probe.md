# Cursor presentation experiment (not a product fix)

`tests/capture/native_cursor.py` is imported only by the existing disposable
hosted scrollbar fixture. It does not enable a production cursor path or qualify
release. Its environment guard is a safeguard; the existing isolated hosted job,
owned-fixture checks and bounded process supervisor remain the execution boundary.
Never run this experiment on the user's desktop.

## Why the existing worker probe is insufficient

Microsoft documents [cursor shape and visibility](https://learn.microsoft.com/en-us/windows/win32/menurc/about-cursors)
as input-thread state. The passive input-transparent selection surface leaves
mouse ownership in the underlying application. A balanced `ShowCursor` call on
an unrelated broker thread does not establish that its visible cursor was hidden.
The worker receipt now separates cleanup from visible-hidden-restored, ineffective,
and inconclusive-hidden-baseline outcomes.

## Candidate and visible baseline

The candidate [MagShowSystemCursor](https://learn.microsoft.com/en-us/windows/win32/api/magnification/nf-magnification-magshowsystemcursor)
changes global cursor visibility without replacing system cursor shapes. It is
not reference-counted and has no documented matching state getter. A hidden
`GetCursorInfo` observation does not identify the reason for hiding, so the probe
never forces this global API visible just to create a baseline.

Instead, if the original cursor is hidden, baseline preparation requires
[GetCapture](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getcapture)
to identify the exact owned popup as the calling GUI thread's native capture
owner. It sets only that thread's cursor to the shared stock arrow and increments
its visibility count exactly once. Both changes are restored in `finally`, with
an exact count check and a global state comparison. No input is injected, cursor
position changed, capture acquired, input queue attached, or persistent preference
changed. Without owned capture, or if one increment is insufficient, the result
is inconclusive, not a pass. No unbounded show/hide loop is permitted.

Only an observed visible baseline may enter the Magnification experiment. It
initializes the API, attempts one hide, observes the result, and restores visibility
and uninitializes in nested cleanup paths, including on observation failure. The
receipt contains flags and null/non-null handle class only; native handles,
coordinates and foreground identities are compared transiently, not serialized.
Popup preservation is checked during and after the experiment. Baseline restoration
also checks the original flags, handle, coordinates and foreground. Unconfirmed
cleanup prevents the fixture from reporting safe retirement.

## Limits and next evidence

Portable fault-injection tests validate call ordering, bounded counts, refusal,
failure cleanup, receipt classification and preservation assertions. They are not
native cursor qualification. A hosted run must report an observed visible-hidden-
restored transition and all preservation/cleanup checks before this candidate has
any positive native evidence. If native capture ownership is unavailable, a new
owned fixture arrangement is needed; forcing unrelated cursor state is not a
fallback.

Even a positive result does not justify production integration: interaction with
accessibility software, concurrent global visibility changes, process failure,
API latency and release-time restoration still need a design and native evidence.
The experiment relies on the existing outer process deadline for native-call bounds
and adds no sleeps, input events or relaxed product deadlines. It is not hardware,
mixed-DPI, arbitrary-application, or crash-recovery qualification.
