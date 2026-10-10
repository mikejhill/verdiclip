# Changelog

## [0.3.0](https://github.com/mikejhill/verdiclip/compare/v0.2.1...v0.3.0) (2026-10-10)


### Features

* ask before closing a capture that wasn't saved or copied ([#6](https://github.com/mikejhill/verdiclip/issues/6)) ([7729379](https://github.com/mikejhill/verdiclip/commit/772937997a89fc92568739db0ccb8e70ad1bc188))
* offer VerdiClip in Explorer's Open with menu for images ([#9](https://github.com/mikejhill/verdiclip/issues/9)) ([cafb1bc](https://github.com/mikejhill/verdiclip/commit/cafb1bc33617ebf5133fd60a6f8f74bdffb91eca))
* rotate, flip, and resize images in the editor ([#11](https://github.com/mikejhill/verdiclip/issues/11)) ([fd8209b](https://github.com/mikejhill/verdiclip/commit/fd8209b7be86c810bc33b0e12714173aa467c40a))


### Bug Fixes

* highlight windows that span monitors on every monitor ([#7](https://github.com/mikejhill/verdiclip/issues/7)) ([bc5f032](https://github.com/mikejhill/verdiclip/commit/bc5f032dbba036fce70642edbc1c74a4c0b4b43d))
* re-apply run-at-login and Open with registrations on launch ([#12](https://github.com/mikejhill/verdiclip/issues/12)) ([c466b1c](https://github.com/mikejhill/verdiclip/commit/c466b1c3620be19618cc749d9608949e69ab1ab0))
* word the close prompt for what would be lost and add S/N shortcuts ([#13](https://github.com/mikejhill/verdiclip/issues/13)) ([3cac693](https://github.com/mikejhill/verdiclip/commit/3cac6932653899e2d086433989255f397d3163c7))


### Documentation

* simplify attribution to a plain credit ([#14](https://github.com/mikejhill/verdiclip/issues/14)) ([8ea308c](https://github.com/mikejhill/verdiclip/commit/8ea308cc5ee088d8b5cd29f8fd72609bb9c7ba03))

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
