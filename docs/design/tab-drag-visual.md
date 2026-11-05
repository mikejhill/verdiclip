# Carried tab visual

The editor smoke feedback identified a missing affordance: the original tab
driver moved its cursor and insertion indicator but rendered no carried tab.
The new visual snapshots the actual complete rendered tab header once after
the drag threshold. It follows the original grab point through reorder,
outside-window movement and cross-window movement. It carries the header,
including native styling/title/close presentation; it is not a live editor
window thumbnail. The delivered `editor-smoke-18c696d3` snapshot stays unchanged.

The preview is a cached pixmap in a separate frameless Tool widget. Its flags
reject activation and input, and it has no focus policy. The native input lease
still belongs to the original tab driver; no QDrag nested event loop, document
view reparenting or ownership transfer is added. Logical pointer coordinates
and the pixmap's original device pixel ratio remain explicit. Moves reposition
one widget without recapturing or reallocating the tab image.

Release removes the preview before publishing the release signal and deferred
atomic transfer. Cancel, source hiding/disablement and allocation failure retire
it without removing a tab. Existing generation, close, job, undo and rollback
rules still govern document movement. Late callbacks cannot recreate a retired
preview. The source is checked again after snapshot/preview construction so a
reentrant cancellation cannot publish a new drag.

## Evidence limits

Offscreen tests cover carried geometry, supplied DPR 100/125/150/175/200/250%,
negative global positions, cached-image reuse, terminal ordering, cancellation,
source hide/disable, visual allocation failure and release of a fake input lease.
These test logical geometry and ownership contracts, not physical displays.
The first QLabel implementation normalized the getter's DPR to the current
screen; a custom pixmap painter now retains the actual snapshot/DPR directly.

An attempted offscreen `QApplication.widgetAt` test returned None and emitted
"This plugin does not support setting window masks". That is retained as a
plugin limitation, not a passing transparency result. Its unchanged hit-test
assertion is now an explicit handoff-only native test, not part of portable
qualification. Actual Windows hit testing under the visible preview, capture
lease continuity, focus preservation, cross-monitor mapping and visual quality
still need bounded native qualification. No new local UI/input run is authorized
or performed by this change. Do not enable readiness from portable results.

## Scrolling-pane scope

The broader combined change includes scrollable-pane discovery through Windows
UI Automation's vertical ScrollPattern, semantic scrollbar targeting, explicit
viewport selection, acquisition/stitching, partial review and ordinary-document
delivery. Ordinary entry can fall back to the existing window capture path;
that does not establish arbitrary automatic scrolling for providers without
ScrollPattern. The editor-only smoke composition constructs none of these
services. Real provider compatibility and capture-to-output behavior remain
separate end-to-end qualifications; scrolling is not enabled by this visual fix.
