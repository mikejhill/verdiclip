# Freeze-first menu qualification

This is an automated comparison harness, not a claim of physical-mouse or
arbitrary-application qualification. The production capture changes are described
in [capture input ownership](capture-input.md).

## Fixed comparison

`.github/workflows/frozen-menu-native.yml` runs the same candidate-owned harness
against these immutable source trees, on disposable Windows 2025 runners:

- Candidate: the exact draft pull-request head.
- 0.12.1: `a1c6bbe488284cf5f5b4ed7a8b1e30181e0f62ed`.
- 0.13.1: `1f73bde2b6ab5ab2324ce8079159004ee2713d3a`.

Each source uses Python 3.13 and separate Qt 6.11.2 / 6.12.0 jobs. Every observation
is a fresh process. Imported grabber, overlay, service, hotkey and controller files
must match the selected checkout's committed bytes. The harness never patches the
historical product source. No failed observation is rerun. Fixed cases continue after ordinary failures only when independent input cleanup
is confirmed. The job retains every first attempt and fails if any required
observation fails. Cleanup uncertainty stops further input cases, including the
later legacy native test step. Process deadlines are 30 seconds, with a six-minute outer step
bound. Setup and action each have five-second postchecked bounds; these are test
lifecycle limits, not new product performance budgets.

The fixed cases are:

1. Application-owned Qt popup: real chord, freeze, native drag, final pixels.
2. Foreign-process Qt popup: the same path, including real popup dismissal.
3. Foreign-process Win32 HMENU: `TrackPopupMenu`, verified owned `#32768` HWND,
   text-patch pixels and the same native input path.
4. Application-owned and foreign-process Qt popup: native Escape and no delivery.
5. Actual tray menu's region command: native Enter, then native selection; the
   initiating tray menu must be absent from the delivered pixels.
6. Active window and fullscreen: genuine registered chords, final marker pixels,
   corresponding physical target bounds, title and editor focus.
7. Repeat: prime real active-window history, reopen the menu, use the genuine
   repeat chord, and check target metadata and selected new tab where supported.
8. Freeze fault: one deliberately injected source error, one native chord, no
   image or live selection, and complete owned cleanup.

The existing popup/modal native matrix and tab destination/focus tests also run on
the candidate. Existing whole-app coverage, static checks, aggregate timing tests,
security scanning and other gates are unchanged. Cold-start timing diagnostics
remain informational; this harness does not claim to fix cold startup.

## What is actually measured

A real `HotkeyService` calls production `RegisterHotKey(NULL, ...)` for disposable
Ctrl+Shift+F18..F21 chords. `SendInput` generates the keyboard input. A nonconsuming
observer, installed after the production filter, records the actual Qt category
and hotkey identifier before production consumes the message. The harness accepts
both `windows_generic_MSG` and `windows_dispatcher_MSG`; it does not manufacture a
WM_HOTKEY or require a particular category. Signal and frame counts reject missing
or duplicated work.

The Qt popup has two opaque, theme-independent colors painted by a QWidgetAction.
An independent real MSS probe waits within the setup deadline for compositor
readiness and verifies every pixel in the pretrigger marker. The first and last
pixel-readiness states are retained; no action has been sent at this stage.
The HMENU case instead verifies contrasting native text in its known first menu
item. Only these owned interior patches are hashed. After the actual menu hides,
ordinary native absolute movement and button input select a rectangle around the
patch. The delivered patch must exactly match the pretrigger patch. The system
cursor's actual coordinates must match the capture rectangle. The fixture's popup
closure is observed behavior, not a requirement that all real menus stay open or
close after capture.

Baseline menu preservation is recorded honestly. Foreign menus can already work
on 0.12.1 because its screenshot precedes overlay activation. The own-Qt-menu
comparison records whether a popup still existed at the freeze boundary and
whether its final pixels survived; it does not require a baseline to fail or add
a special delay to make it fail. The candidate requires all requested menu patches
to survive; initiating tray-command patches must not survive.

Input is submitted only while foreground PID belongs to this process or its
verified child fixture. Partial input errors still release all submitted keys and
buttons. Cleanup independently closes the controller, unregisters shortcuts,
removes observers, reaps the exact child, stops fixture timers and releases screen
resources. An atomic ledger precedes each native submission. After every observation exits
and is reaped, an independent cleanup process has ten seconds to release only its
recorded test keys/button. A hard-timeout or ordinary failed observation remains
failed even if this cleanup succeeds. Cleanup failure/hang has an explicit receipt
and prevents any later input test from running. Outstanding selection/native
overlay handles or cleanup errors fail.
The first action error is retained even if cleanup also fails.

## Privacy and limits

Only synthetic fixture patch hashes and small JSON/text receipts are uploaded.
No desktop screenshots, private application text, input traces from other apps,
settings from the installed application, or clipboard contents are stored. The
full-screen synthetic backdrop covers the fixture monitor. Capture itself still
uses the production virtual-desktop source in memory.

The window source is restricted to the owned fixture's real native frame instead
of enumerating unrelated windows. Deterministic tests separately verify that
copied target metadata cannot change after the frame is prepared. Automated
SendInput proves ordinary native coordinates and routing, not physical mouse feel,
all hardware speeds, arbitrary applications, mixed-DPI hardware or every native
menu implementation. Those limits must remain explicit in release assessment.

Portable tests in `tests/portable/test_frozen_menu_native.py` reject corrupted
pixels, malformed geometry, late success, partial-input leaks, ownership loss and
child-start cleanup failures. They validate the harness, not native Windows behavior.
