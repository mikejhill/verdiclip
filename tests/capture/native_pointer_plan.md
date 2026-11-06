# Ordinary native pointer candidate (not release-qualified)

Base: integrated PR91 tree `28b5b87287609b1accdbaf8d1a44e4bba68cfca0`.
`CaptureService.enable_native_pointer()` explicitly enables this isolated candidate
while idle. Default construction and preexisting protected native GUI use the
existing passive virtual-pointer implementation. That fallback still has the
reported duplicate arrow/crosshair and does not qualify as a complete fix.

## Routing

Owned overlays are hittable, topmost and nonactivating. Qt retains native
CrossCursor handling. Only actual owned client WM_SETCURSOR observations queue
admission; the queued callback rechecks exact hit, original recipient identity,
foreground/focus, integrity, and absence of capture/menu/move loops. The immutable
per-request route cannot reopen after cancellation. Natural admission has a
one-second observation bound beginning when owned HWNDs are published. Failure
cancels; no synthetic movement or cursor message is used to establish admission.

The low-level hook is the sole semantic input source in native mode. It forwards
only guarded MOVE packets and publishes their absolute physical coordinates.
RawInput does not independently apply deltas or button actions. Qt widget mouse
handlers do not independently change selection. Buttons and keys keep matched
release ownership and are not forwarded as clicks. Arrow keys request a guarded
one-pixel (Control: ten-pixel) physical move and exact readback; failure cancels
without retry. No admission/teardown pointer reposition is allowed.

Foreground/capture changes revoke selection and drain pending pairs. Checking
and forwarding are not atomic with another process acquiring capture; controlled
transition tests must distinguish natural movement routing from leaked swallowed
button releases. No preexisting captured/menu UI is admitted to this path.

## Qualification gates

- Portable actual worker controls: raw/hook order, absolute movement, nudge
  readback, semantic cancellation before GUI drain, overflow, and matched release.
- Native owned fixture: stationary natural admission, same OS/selection point,
  unchanged foreground/focus, native CrossCursor instead of duplicate stationary
  arrow, normal/cancel/repeated sessions, keyboard edge behavior and transition
  cancellation. Fixture movement is simulated, not physical-input evidence.
- First terminal cursor observation follows HWND/input retirement. Compare known
  static bitmap AND/XOR appearance and hotspot (not handle alone); reject unknown
  formats. No polling until success, no saved foreign cursor writes.
- Abrupt owned-process exit independently checks first terminal appearance and
  foreign state. A normal cleanup result does not qualify a crash.
- Menu preservation remains the unchanged virtual path and is explicitly not a
  duplicate-pointer fix. Rendered-pointer verification is separate from bitmap
  and logical coordinate evidence. No desktop run, release, or integration is
  authorized by these portable checks alone.

## Current hosted subset and limits

The external WinForms fixture constructs one deterministic static monochrome
16x16 cursor using CreateCursor. Its AND/XOR mask and hotspot are independently
checked before selection and at the first terminal observation. This one handle
is intentionally retained until confirmed exact fixture process exit, rather
than destroying a possibly in-use cursor. No cursor-state repair is performed.

The current bounded hosted subset covers stationary admission before any
post-start input, absolute movement, keyboard nudge, drag, cancellation and four
repeated sessions. Setup and subsequent moves are explicitly SendInput, not
physical input. Bounded external menu cases check preexisting fallback preservation and
post-admission revocation with a fresh correlated final observation. No
mixed-monitor or desktop-rendered uniqueness claim is made by this subset.
During expected crosshair identity uses the same shared LoadImageW IDC_CROSS
resource/flags as Qt6.11.2 and6.12.0 Windows backend. This is positive resource
identity, not static provenance or rendered uniqueness. Sources:
https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/windows/qwindowscursor.cpp#L438-L479
https://github.com/qt/qtbase/blob/v6.12.0/src/plugins/platforms/windows/qwindowscursor.cpp#L438-L479

The abrupt-exit lane uses one multiprocessing spawn child with exact runtime/PID
attestation before QApplication or selection. CPython Windows spawn bypasses the
venv redirector and retains the CreateProcess handle. A same-GUI-thread fresh
OWNED/live-HWND/foreground-safe proof precedes a correlated crash-entry and
self-TerminateProcess97. Parent requires that exact exit and HWND retirement
before its first cursor/foreign-state observation. Failure cleanup never counts
as successful crash restoration. Fixed timeouts and exact-child retirement are
independent of cursor verdicts. Those remain
qualification gaps. The candidate stays opt-in and cannot qualify a full release.
