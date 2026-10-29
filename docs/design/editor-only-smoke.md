# Editor-only smoke build

This separate composition runs the real document workspace, editor, annotation
history, tab transfers and save/close behavior. It does not construct the normal
application controller, capture service, desktop grabber, scrolling workflow,
tray, global hotkeys, startup integration, associations or updater. Settings,
styles, default output, logs and instance identity belong to its bundle.
Both native readiness flags stay false. This is not capture qualification or
completed-feature acceptance, and does not authorize merge, release or install.

## Mike's smoke session

1. Run the bundle's `launch-editor-smoke.cmd` yourself. Two synthetic documents
   open by default. No user images are read automatically.
2. Try annotations, undo/redo, zoom, tab reorder, detach into a second window,
   transfer between windows, and the Window menu and tab keyboard shortcuts.
3. Use File > Open to choose your own image. It is imported as a copy: normal
   Save uses the bundle's `output` folder. Save As can write to a location you
   explicitly choose. Copy/Paste use the system clipboard only when you invoke
   them. Printing, settings, help links and About are disabled in this smoke UI.
4. Close every editor window to quit. Edited documents retain normal
   Save/Discard/Cancel prompts. There is no tray process left running afterward.

The installed application can stay installed and its preferences are untouched.
This composition has no global hotkeys and its local IPC name is different.
It does not register or replace the installed app. Rollback is simply closing
all smoke windows; private state and outputs remain in the smoke bundle.

The bundle manifest records its exact source commit and hashes, its validated
parent commit, dependency runtime location/versions and disabled features. It
uses a snapshot of current reviewed source, not an old feature wheel. The
existing private Python/Qt runtime is reused; retain the referenced runtime
folder alongside this bundle. `self-check.cmd` checks imports/versions without
creating QApplication, windows or capture services.

## Validation and limits

Offscreen tests forbid normal-settings lookup and live service construction,
exercise real synthetic-document detach, verify disabled actions on detached
windows, and prove imported-source bytes remain unchanged after default Save.
The existing verification tests retain their original full-preview readiness
guard. A successful editor-only launch cannot enable that full-preview path.

Native drag behavior, DPI mapping, physical pointer behavior, UIA, capture,
scrolling, real hotkey registration and screen-reader usability remain separate
qualification. The known broker-driver target-binding failure remains unresolved
and its original failed receipt is preserved. The editor smoke composition does
not reinterpret that failure as a pass.
