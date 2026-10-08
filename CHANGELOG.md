# Changelog

## [0.2.1](https://github.com/mikejhill/verdiclip/compare/v0.2.0...v0.2.1) (2026-10-08)


### Bug Fixes

* show capture guides and window highlight before the mouse moves ([#4](https://github.com/mikejhill/verdiclip/issues/4)) ([f80e117](https://github.com/mikejhill/verdiclip/commit/f80e1179785be096bb08c0e5fffed309f5bf6914))

## [0.2.0](https://github.com/mikejhill/verdiclip/compare/v0.1-legacy...v0.2.0) (2026-10-07)


### ⚠ BREAKING CHANGES

* rebuild VerdiClip around an immutable document model ([#1](https://github.com/mikejhill/verdiclip/issues/1)) ([f1f43a9](https://github.com/mikejhill/verdiclip/commit/f1f43a9fb24457fc0ad52552b5044e003e133bbf)). Settings move to `%APPDATA%\VerdiClip\settings.json` with a new schema; the pynput and Pillow dependencies are removed; the CLI options `--monitor`, `--format`, and `--quality` are gone.

### Features

* ground-up rebuild: immutable document with a non-destructive crop, every change undoable, and one renderer for the canvas and exports ([#1](https://github.com/mikejhill/verdiclip/issues/1))
* frozen-screen capture across all monitors with a magnifier, window picking, and repeat-last; global hotkeys via `RegisterHotKey` with conflict reporting
* editor with select, crop, rectangle, ellipse, line, arrow, text, counter, highlight, obfuscate, and freehand tools
* type labels inside rectangles and ellipses, starting right after drawing
* per-tool colors, fills, widths, and fonts remembered across screenshots
* combine after-capture actions: open in the editor, copy to the clipboard, save to a file
* light, dark, and match-Windows themes; settings reachable from any editor (`Ctrl+,`)
* single instance with file forwarding, run at sign-in, and a `capture`/`open` command line

### Documentation

* README with screenshots, design philosophy, UX contract, contributing and release guides ([#1](https://github.com/mikejhill/verdiclip/issues/1))
