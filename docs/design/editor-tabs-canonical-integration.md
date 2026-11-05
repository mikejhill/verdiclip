# Canonical capture integration and private build checkpoint

Historical checkpoint: [capture 18e72 integration](editor-tabs-capture18-integration.md)
records the current merged source, passive presentation API and validation.

The tabs branch imported the scrolling foundation through the exact approved
canonical source `f25091d7af711d89db5a6e5d30f569a7f89fed16` with `cherry-pick -x`.
Local corresponding commits: 6e109cc (8adc357), 99b18fe (2be572b), 066d3fc (a2e7e7d),
c007a84 (f25091d). Capture contract, scrolling modules and their tests are byte-for-
byte unchanged from the supplied source. No experimental native input code was
imported. These copies preserve source provenance; parent integration need not
reapply them onto a branch that already contains the original scrolling commits.

There is exactly one ReviewedCapture class, in capture.reviewed. The former
document-owned duplicate was removed. CaptureProvenance projects capture_id,
created_at, actual enum kind/completeness, title and optional stop reason without
retaining pixels. The controller keys retry/completion by capture_id and uses
created_at for output naming. The ordinary adapter produces SCREENSHOT/COMPLETE;
actual ScrollSession.accept output passes directly through the same controller.
Repeated acceptance returns the same object/ID, repeated delivery creates one
copy/file/tab, equal pixels with distinct capture IDs remain separate documents,
and producer spool closure cannot destroy admitted image data. Delivery settings,
background focus, newer-edit dirty guards and independent output receipts remain.

## Combined offscreen qualification

Python 3.13.5 on Windows. Native capture/tab tests are excluded; actual desktop
capture, global keyboard hooks, registry associations and shell shortcuts are
explicitly deselected as in the earlier qualification record.

| Runtime | Pass / skip / deselect | Coverage | Random seed |
| --- | --- | --- | --- |
| Qt/PySide6 6.11.2 | 1,348 / 1 / 14 | 94.95% | 3073001825 |
| Qt/PySide6 6.12.0 | 1,348 / 1 / 14 | 95.01% | 2971581209 |

Changed executable lines against v0.12.1: 1,871 / 1,926 = 97.14% on minimum Qt.
Formatting, Ruff and strict ty pass after the annotation-only Generator update.
The earlier targeted mutation record remains 264/318, plus the separately verified
18 survivor rejections. No score is claimed for all combined or new preview code.

## Private verification composition

`verdiclip.verification` is a separate composition root. PreviewProfile derives
settings, styles, output, log paths and an instance namespace from the bundle and
commit. Different bundle roots do not forward to each other. PreviewController
uses production document behavior with empty global hotkeys, no settings keyboard
hook, no updater, private registration adapters that refuse writes, and constrained
settings that cannot enable integrations. Direct make-default also cannot open
Windows Settings. Existing normal settings and registered integrations are never
consulted or modified. Tray and all editor hosts show Preview + short SHA without
altering document/output names. Logs stay inside the bundle and handlers close on
exit. Normal application entry behavior remains unchanged.

The launcher defaults to a noninteractive self-check: paths/runtime only, no
QApplication, screen source, hook, service startup or state creation. A bundle
manifest with native_ui_ready=false refuses --run-ui before GUI construction; it
has no CLI override. Offscreen preflight covers path/namespace separation, settings
policy, guard adapters, labels across detach, simulated source composition, forwarding/
reservation failure, incompatible Qt app, self-check and readiness refusal.

The built artifact is a checkpoint, not user-ready proof of the complete scrolling
UX. The imported scrolling code is the reviewed-image foundation; production native
scroll acquisition/UI and no-dismissal screenshot input are still capture-owned
acceptance gates. Do not enable native launch merely because the model tests pass.
Parent arranges independent Codex PR review and Mike's local verification before
merge. No tabs publication approval is inferred from the scrolling branch's push.

## Remaining native window

Parent must coordinate an exclusive desktop slot after the capture backend is
ready. Qualify final-head ordinary/scroll capture while Qt menus, owned dialogs and
foreign native popups remain visible/actionable; tab input quiescence and resumed
focus; physical reorder/detach/reattach, last-tab moves, Escape/hotkey/capture-loss
cancellation; both Qt baselines/current candidate, mixed DPI/multimonitor placement,
keyboard/Narrator and the handled COM activation diagnostic. No native desktop
input or prototype driver was operated during this migration.


## Prepared local artifact

Source/build commit: 8616e39542c150304c6723978c539a6945dfd76f. Private bundle:
`editor-tabs-qualification/combined-preview-8616e395` under the task workspace.
The wheel is installed only in that bundle's runtime, with Python 3.13.5,
PySide6/Qt 6.12.0 and mss 10.2.0. Wheel SHA-256:
`1ce56136ce470833b282669e8aa4883c552ad988860d46fd296a0471561797b0`.

Actual installed-wheel self-check passed, printed the private import/runtime paths,
left QApplication absent, and created no settings/output/log directories. Manifest
and launcher hashes are recorded. The normal installed app was untouched. Normal
entry-point executables were removed only from the newly created private runtime,
so its dedicated launcher is the intended entry. Native UI readiness remains false;
no UI, capture, hook or input driver was launched from this artifact.

Socket-only handoff for the scrolling branch:
`a9102abcfc4cd1ec332468c28a79b108de2db19b`, parent v0.12.1, only
shell/instance.py and tests/shell/test_instance_lifetime.py, with Codex attribution.
An exact two-file patch is also saved in the task's editor-tabs-qualification folder.
It has no tabs/document/provenance dependency and does not change the tabs worktree.
