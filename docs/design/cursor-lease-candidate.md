# Isolated cursor visibility candidate, not release qualification

This draft changes the production native input adapter, unlike the earlier
hosted-only cursor experiments. It must not be merged or released as a qualified
repair on the strength of API return values or logical cursor flags.

## Scope and lifecycle

`platform/cursor.py` supplies a one-shot `CursorVisibilityLease`. The native input
thread creates it, installs its existing hooks/raw receiver, then attempts cursor
suppression before announcing readiness. Completion, cancellation, setup errors,
message-pump errors, heartbeat expiry and deadline expiry all reach the existing
worker cleanup path. The application quit path already requests bounded cleanup.

A successful MagInitialize creates an explicit obligation to call
MagShowSystemCursor(TRUE), including when the attempted hide reports failure or
raises. MagUninitialize is attempted even if restoration raises. Cursor cleanup
failure never bypasses input cleanup. An unsuccessful or exceptional teardown
stays unconfirmed, blocks normal publication/reuse, and cannot become successful
merely by calling close again. There is no retry loop, new timeout, background
helper, elevation, activation, input attachment, cursor-shape substitution or
saved preference change. Existing deadlines are unchanged, but cannot preempt a
blocked native API call.

Logically hidden, touch-suppressed and unknown flag combinations cause no
Magnification writes. CURSOR_SHOWING is only an eligibility heuristic: it does
**not** establish the previous Magnification visibility state. No getter or
ownership token is available for that state.

## Unresolved compatibility and recovery risks

[MagShowSystemCursor][show] changes an uncounted global state. Explicitly showing
again can override a different client's pre-existing or concurrent hide. The
portable test deliberately demonstrates that unresolved incompatibility while
logical CURSOR_SHOWING remains unchanged. A successful cleanup receipt means the
explicit API calls returned success, not that another client's state was restored.

[MagUninitialize][uninitialize] documents destruction of runtime objects, not
cursor rollback. Microsoft's [fullscreen sample][sample] comments on removal of
magnification and color effects; it never calls MagShowSystemCursor. Neither that
sample nor the API contract establishes cursor restoration on abrupt process
termination. The current bounded worker and ordinary finally paths do not solve
process crashes, forced termination or concurrent accessibility-client ownership.
These are release blockers, not relaxed preservation requirements.

The hit-testable overlay alternative does not solve arbitrary popup ownership.
[WM_SETCURSOR][setcursor] requires native pointer movement without mouse capture.
The broker consumes movement and retains a frozen OS anchor, while an existing
foreign popup can keep capture. Sending the message ourselves does not establish
native pointer ownership, and stealing capture would break preservation.

## Verification boundaries

Deterministic tests cover the real lease's ABI bindings, ordering, hidden-state
no-write path, failed initialization/hide, explicit restoration, uninitialization,
exception cleanup, single-use lifetime and failed-cleanup latch. The native
adapter's fake-DLL tests fill a showing baseline and verify cursor cleanup cannot
skip hook/raw retirement. A portable UX journey verifies the virtual crosshair
remains rendered independently of the OS anchor; it cannot verify OS hiding.

The existing hosted WPF scrollbar matrix records a separate `cursor_leases`
receipt for every actual production input platform. Its successful path requires
production suppression and restoration/uninitialization to have returned success.
A hidden-baseline skip, a constructed adapter or the earlier standalone probe
cannot satisfy that gate. Only the existing owned-fixture MOVE may prepare the
baseline. No new baseline forcing, native input flow or workflow is introduced.
Popup, capture, foreground, input and cleanup assertions remain intact.

These receipts explicitly mark rendered presentation unverified and previous
global state unknown. A user-observed before/during/after presentation check is
still required; it would not resolve the competing-client or crash risks above.
No native execution or presentation qualification has been performed by this
cloud implementation task.

[show]: https://learn.microsoft.com/en-us/windows/win32/api/magnification/nf-magnification-magshowsystemcursor
[uninitialize]: https://learn.microsoft.com/en-us/windows/win32/api/magnification/nf-magnification-maguninitialize
[sample]: https://github.com/microsoft/Windows-classic-samples/blob/main/Samples/Magnification/cpp/Fullscreen/FullscreenMagnifierSample.cpp#L55-L61
[setcursor]: https://learn.microsoft.com/en-us/windows/win32/menurc/wm-setcursor
