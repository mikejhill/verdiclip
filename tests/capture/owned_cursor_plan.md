# Ordinary-window ownership component

This isolated branch is an intermediate component, not a complete cursor fix.
Foreign menu/capture cases remain refused, and release remains blocked on their
solution. No product capture behavior is changed by this skeleton.

## Source and scope

Base: d94a1f0d4f3e70b1ccf3f6d6c4a291accd2f58fa.
Base tree: 993f522930db5459b43aa316cb51601b48be8f28.
The injected policy has no Win32 binding. The isolated native adapter binds the
owned surface and per-thread visibility API only behind the hosted gate.

## Planned native adapter and finite acceptance

1. A disposable owned process starts a dedicated GUI thread. The supervisor
   retains exact process/thread/HWND ownership and callback owners until retirement
   is confirmed. No Qt activation or foreign input-queue attachment.
2. Independently sample visible global cursor shape/position and foreign foreground,
   focus, capture, menu and recipient integrity before any owned window creation.
   Do not require class cursor handles to equal the global cursor handle.
3. Refuse foreign capture/menu/move-loop or unknown proof before surface creation.
   Create a nonactivating hittable owned HWND without acquiring capture. Keep the
   original foreign recipient proof independent of our HWND winning hit testing.
4. Pump for at most one second for a real WM_SETCURSOR addressed to the owned HWND.
   No synthetic message, mouse movement or focus change. Absence is unsupported.
   In that handler, revalidate foreign state and call the scope once. Return TRUE
   without DefWindowProc cursor changes. A returned ShowCursor(FALSE) count of -1
   admits this scope; other returns trigger exactly one balancing TRUE and refusal.
   Never normalize the counter. Baseline zero was not separately measured.
5. Preserve the broker as the sole input authority. Do not add duplicate Qt input.
   Foreign state changes revoke the scope and trigger retirement, never takeover.
6. Normal/cancel finally runs on the dedicated thread: balance first, then destroy
   HWND, then retire broker/thread. Retain owners on any uncertain cleanup and stop
   subsequent cases. No saved foreign cursor restoration.
7. The independent observer judges the FIRST post-retirement sample against the
   original global cursor and foreign state. A mismatch remains a failure even if
   later samples recover. Fixed later observations may reveal delayed interference.
8. Finite cases: stationary entry, normal, cancel, repeated sessions for two owned
   foreign clients. Only if all pass, request abrupt self-TerminateProcess on the dedicated owner
   thread while admitted and unbalanced. The supervisor requires the correlated
   crash-entry marker and exact child exit code97. There is no child finally or
   supervisor cursor repair; judge the first post-exit sample likewise.
   A crash cannot promise thread-counter restoration in advance.
9. API visibility/state results are not rendered cursor proof. Rendering and physical
   input qualification remain separate holds; do not call a green workflow support.

## Remaining work

The native adapter and coordinated hosted harness are implemented for independent
review. No native execution or publication is approved before that review. Portable
controls prove accounting and injected lifetime behavior, not native visibility or
physical/rendered behavior. Actual hosted results remain required.
