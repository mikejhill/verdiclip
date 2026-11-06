# Owned cursor surface: admission-only prototype

This isolated branch starts from the reviewed PR91 scrolling tree. No product
source changes. It does not hide the cursor and does not implement a cursor fix.

The first question is whether a nonactivating, hittable owned HWND receives a
natural WM_SETCURSOR while the real broker retains virtual-pointer authority.
The prototype never generates pointer movement, sends cursor messages, acquires
mouse capture, attaches input queues, changes cursor state, or activates its
surface. Fixture setup may position its own window under the existing anchor
before the preservation baseline. It never moves the pointer.

A raw class with a null class cursor avoids Qt cursor handlers. Every
WM_SETCURSOR is consumed with TRUE, never passed to DefWindowProc or a parent.
An admission observation requires exact owned HWND/wParam/client hit, the
physical hit resolving to that HWND, an active broker and unchanged foreign
foreground, GUI recipients, and cursor handle/flags/position. A missing message
is explicitly unsupported. Existing foreign capture refuses before surface
creation. Additional process/integrity checks retain baseline foreign recipients
so our own hittable HWND cannot mask them. Failures latch.

Each normal session observes at most one second; the early-cancel session uses
50ms. Three sessions exercise normal/cancel/repeated ownership. The owned HWND
is destroyed before broker release/drain, including faults. No saved foreign
cursor is restored. Failed native destruction/class retirement retains the callback
owner strongly until harness process exit, raises a terminal harness error and
prevents another session. Any preservation/broker failure also ends the series. Foreign menu fixture state must remain preserved. Native
exceptions do not become success. Receipts contain fixed classifications,
counts and booleans only, never desktop pixels or raw native handles.

A completed job may report unsupported. It establishes only a finite experiment,
not cursor visibility, crash recovery, mixed-DPI support, physical input or a
release gate. Crash and mixed-DPI cursor suppression checks are explicitly not
run because there is no suppression operation yet. Even natural ownership does
not prove a rendered hidden cursor. Any next-stage mutation needs separate
review, meaningful preservation tests and a supported presentation oracle.
