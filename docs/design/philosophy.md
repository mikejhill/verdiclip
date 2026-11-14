# VerdiClip: Purpose, Philosophy, and Architecture

This is the founding document for the VerdiClip rebuild. It records why the
first implementation was replaced, what the product is for, the principles
every change must respect, and the architecture that follows from them.

---

## 1. Why rebuild

The first implementation (tag `v0.1-legacy`, branch `main` before the rebuild)
worked and had 795 passing tests, but 28 commits in three days were mostly
"fix N bugs" rounds. The bugs came from the architecture, not from careless
code:

| Symptom | Root cause |
| --- | --- |
| Adding a tool meant editing 6+ files (`history.py`, `serialization.py`, `handles.py`, `window.py`, `canvas.py`, the tool) | Annotation state lived inside Qt `QGraphicsItem` subclasses. Every feature that needed "what is this item?" grew its own `isinstance` ladder. |
| Undo bugs: "crop undo", "arrows disappear after crop", nudge not undoable | Tools mutated the scene first and registered undo afterwards (`_already_added`, `_first_redo` flags). Anything that forgot to register was silently non-undoable. |
| Obfuscation pixelated the wrong area after a crop | Crop destroyed the background item; obfuscation items kept a reference to the old one. Crop was destructive. |
| `Ctrl+PrtSc` triggered both region and full-screen capture; Windows' own PrtSc handler also fired | `pynput` keyboard hook with subset matching (`{PrtSc} ⊂ {Ctrl, PrtSc}`); hook cannot consume the key. |
| Window-picker hotkey configured but never registered; `RepeatCapture` class never used; `include_cursor`, `default_action`, `run_at_login` settings shown but ignored | No single owner of the capture workflow; the tray icon was the de-facto controller. |
| Overlay misaligned on scaled displays | mss returns physical pixels, Qt widgets use logical pixels; one overlay spanning a mixed-DPI virtual desktop can't be correct. |

The tests were extensive but mostly asserted on internals (private attributes,
item types), so they locked in the structure rather than the behavior and did
not catch the UX defects users reported.

## 2. Core purpose

> **Get a precise piece of the screen, make a point on it, and put it where it
> needs to go — in seconds, without thinking about the tool.**

Three verbs, in order of importance:

1. **Capture** exactly what the user saw at the moment they asked.
2. **Annotate** quickly with a handful of clear, predictable marks.
3. **Deliver** to the clipboard, a file, or a printer.

Everything else (settings, CLI, tray) exists to serve those three. A feature
that does not make one of them faster or more correct is out of scope.

## 3. Principles

### P1. The user's flow is the product (UX first)

- **Zero-friction path:** hotkey → select → `Ctrl+C` (or `Enter`) → done. The
  default after-capture action and every editor default are chosen to make the
  most common path the shortest.
- **Keyboard and mouse parity:** every action reachable by mouse has a
  shortcut; every shortcut is discoverable (menus, tooltips, status bar hints).
- **Escape always backs out exactly one level** and never destroys work;
  **Enter moves one step forward.** Every tool shares one ladder of levels
  (typing → gesture → drawing tool → selection → rest), defined in the
  [keyboard model](ux-contract.md#keyboard-model). A new tool fits that
  ladder rather than inventing its own key behavior.
- **Nothing is lost silently:** closing an editor with undelivered changes asks;
  a hotkey that can't be registered is reported, not logged and forgotten.
- **What you see is what you get:** the canvas and the exported image are drawn
  by the same renderer.
- **Latency budgets are requirements**, not aspirations (see §6).

### P2. UX is designed before it is built and tested after

- Every interaction is specified in [`ux-contract.md`](ux-contract.md) before
  code is written. The contract is the acceptance criterion.
- Every contract item has an automated **journey test** that drives real widgets
  with real mouse/keyboard events and asserts on what the user would observe
  (pixels, clipboard contents, files, window state) — never on private state.
- Journeys also produce **snapshots** (`uv run poe ux-snapshots`) so a human
  can review each step visually.
- Automated CI covers as much behavior as it can reliably verify. Physical-machine
  checks (real `PrtSc` key, multi-monitor DPI) are exceptional, ad hoc checks for
  critical gaps, selected from the contract's manual checklist under the
  [release policy](../releasing.md#before-merging-a-release-pull-request).

### P3. One source of truth: the document model

- An edit session is a `Document`: an immutable base image, a non-destructive
  crop rectangle, and an ordered list of immutable annotation values.
- Annotations are frozen dataclasses. Each type knows its own geometry, hit
  testing, handles, and serialization. Adding a tool touches one module plus
  the renderer.
- Qt widgets display and manipulate the document; they never own its state.

### P4. Every change is a command

- The only way to mutate a `Document` is to execute a `Command` through
  `History`. Undo/redo is therefore total by construction.
- Because annotations are immutable, most commands are generic snapshot swaps
  (`ReplaceAnnotations(before, after)`), so move, resize, restyle, nudge, and
  paste share one tested implementation.

### P5. Capture is faithful

- The screen is frozen at the moment of the hotkey; the user selects from that
  frozen image and gets exactly those pixels.
- Pixels are physical. Each monitor gets its own overlay at its own DPI.
- Global hotkeys use the OS mechanism (`RegisterHotKey`) so combinations are
  exact and the key is consumed.

### P6. Small, strict, and boring code

- Python standards: uv, ruff (broad rule set), ty with every rule at error,
  poe tasks, `src/` layout, classes over functions, typed dataclasses at every
  boundary, ≥ 90% branch coverage. `uv run poe check` is the gate.
- Platform-specific code (Win32 via `ctypes`) lives only in `verdiclip.platform`
  behind typed adapters, so the rest of the code is testable on any machine.
- No speculative features. Settings exist only if the code honors them.

## 4. Scope

**In (1.0):** universal capture (drag a region, click a window, modifier
actions) plus active-window, monitor, repeat and scrolling capture, all from a
frozen, DPI-correct overlay; editor with select, crop, rectangle, ellipse, line,
arrow, text, counter, highlight, obfuscate, freehand; rotate, flip, resize;
undo/redo; element copy/paste; zoom/pan; tabs that move between windows;
clipboard, file (Save, Save As, auto-name) and print delivery; tray + global
hotkeys; settings dialog; Explorer Open with / default image app; self-update
from PyPI; standalone CLI `capture` / `open`; `verdiclip ctl` automation of the
running app, with an installable skill for AI coding agents.

**Out (until a user need proves otherwise):** image effects (shadows, torn
edges), uploads to cloud services, OCR, video, plugins.

## 5. Architecture

```text
src/verdiclip/
├── __main__.py        Application: entry point, logging, single instance, mode dispatch
├── cli.py             CommandLine → typed CliRequest (capture / open / ctl)
├── ctl_cli.py         `verdiclip ctl` arguments, parameters and help (the usage reference)
├── exceptions.py      AppError hierarchy
├── settings.py        Frozen Settings dataclasses + SettingsStore (JSON ⇄ dataclasses, migration)
├── geometry.py        Point / Rect value objects (pure Python)
├── startup_lifetime.py Startup cleanup that never hides the original error
├── document/          Pure model, no widgets
│   ├── style.py         Color, Style
│   ├── annotations.py   Annotation types (frozen), labeled boxes, handles, hit testing
│   ├── document.py      Document (image + crop + annotations)
│   ├── admission.py     Metadata for a screenshot admitted to an editor tab
│   ├── commands.py      Command protocol + implementations
│   ├── transform.py     Rotate, flip, resize: pixels, crop, and annotations together
│   ├── history.py       Undo/redo stack, "delivered" tracking
│   └── codec.py         Annotation and Style ⇄ JSON (copy/paste, remembered styles)
├── render/
│   └── renderer.py      The only place that paints annotations (canvas + export)
├── editor/            Widgets that display and edit a Document
│   ├── session.py       EditorSession: document, history, selection, per-tool styles
│   ├── tools.py         Tool protocol + one class per tool → emits Commands
│   ├── canvas.py        CanvasView (custom paint, zoom/pan, selection UI)
│   ├── inline_editors.py In-place text, counter, and box-label editors
│   ├── resize_dialog.py Resize in pixels or percent, aspect ratio linked
│   ├── style_bar.py     Stroke / fill / width / font controls
│   ├── style_memory.py  Remembers each tool's last style across editors and restarts
│   ├── chrome.py, icons.py Theme-aware toolbar styling, canvas backdrop, vector icons
│   ├── window.py        EditorWindow: actions, menus, toolbars, status bar
│   ├── workspace.py     Owns every window and tab; atomic tab transfers; close plans
│   ├── page.py, contracts.py, factory.py, transfer.py  Movable document views and their hosts
│   ├── tab_bar.py, tab_preview.py  Tab strip, drag owner, drag preview
│   ├── closing.py, close_prompt.py Safe close and the unsaved-work question
│   └── jobs.py          Document-owned background job tickets
├── capture/
│   ├── models.py        CaptureMode, Capture, ScreenGeometry (logical ⇄ physical)
│   ├── grabber.py       Frozen desktop snapshot (mss, physical pixels)
│   ├── overlay.py       Universal capture overlay, one per monitor: drag region or click window
│   ├── gestures.py      What each modifier combination does when universal capture finishes
│   ├── modality.py      Keeps app dialogs from blocking the overlay
│   ├── service.py       CaptureService: freeze → select → Capture; repeat-last
│   ├── scroll_selection.py Point chooser for scrolling capture; runs one helper
│   ├── scroll_helper.py One killable child process per scrolling capture
│   ├── scrolling.py     ScrollCapture: scroll top to bottom, settle, collect frames
│   └── stitch.py        Join frames by row-hash voting; sticky header/footer kept once
├── output/
│   ├── naming.py        FilenamePattern
│   └── delivery.py      Clipboard / file / print delivery
├── automation/        `verdiclip ctl`: local control of the running app
│   ├── protocol.py, client.py  Wire format and the Qt-free client
│   ├── server.py, handlers.py  Per-user named-pipe server on the GUI thread
│   ├── command.py       Runs one ctl request and prints JSON
│   ├── editing.py       Edit operations → one undoable batch
│   ├── capturing.py     Validated ctl capture requests
│   ├── preview.py       Renders a document to PNG for inspection
│   ├── skills.py        Installs the agent skill (skill/SKILL.md)
├── update/            Self-update from PyPI
│   ├── feed.py          Latest release from PyPI
│   ├── install.py       How VerdiClip was installed, and how to update it
│   ├── service.py       Periodic check, hand-off, outcome
│   └── helper.py        Installs after the app exits, then restarts it
├── platform/          Windows-only adapters (ctypes), behind typed interfaces
│   ├── hotkeys.py       HotkeyService (RegisterHotKey + native event filter)
│   ├── keyboard_hook.py Low-level hook for recording hotkeys
│   ├── windows.py       WindowLocator (z-ordered top-level windows, DWM bounds)
│   ├── scrolling.py     UI Automation ScrollPattern target (helper process only)
│   ├── associations.py  Explorer Open with registration
│   ├── shortcuts.py     Named, iconed .lnk files (IShellLinkW via COM)
│   ├── startup.py       Run-at-login registration
│   ├── launcher.py      Console-free way to start VerdiClip
│   └── tab_drag.py      Bounded input lease for dragging tabs
└── shell/             The always-running app
    ├── controller.py    AppController: hotkeys → capture → after-capture actions
    ├── automation_capture.py Runs ctl capture requests in the app
    ├── instance.py      Single instance + forwarding a second launch's files
    ├── settings_dialog.py, hotkey_field.py Settings and the hotkey editor
    ├── default_app_dialog.py, skill_dialog.py Default image app and skill install guides
    ├── warmup.py        Pre-pays first-use costs of the overlay and editor
    ├── theme.py         Light / dark / match-Windows
    └── tray.py          TrayIcon + menu

scripts/ux_gallery.py   Renders key screens in light and dark for design review
```

Data flow:

```text
Hotkey/Tray/ctl ─▶ AppController ─▶ CaptureService ─▶ Capture(image, metadata)
                                                        │
                       after-capture action ◀───────────┘
                       ├─ editor:    EditorWindow(Document(image)) ─▶ Delivery
                       ├─ clipboard: Delivery.clipboard(image)
                       └─ file:      Delivery.save(image, FilenamePattern)

EditorWindow:  input ─▶ Tool ─▶ Command ─▶ History ─▶ Document ─▶ changed ─▶ CanvasView.update()
                                                                  └────────▶ Renderer (export)
```

## 6. Latency and resource budgets

Checked by journey tests (`@pytest.mark.ux_budget`) on the developer machine.

| Interaction | Budget |
| --- | --- |
| Hotkey → overlay visible | ≤ 150 ms |
| Region confirmed → editor visible | ≤ 300 ms |
| Paint during drawing (4K image, 50 annotations) | ≤ 16 ms |
| Flatten + copy to clipboard (4K) | ≤ 250 ms |
| Idle memory (tray only) | ≤ 80 MB RSS |

## 7. Testing strategy

| Layer | Location | What it proves |
| --- | --- | --- |
| Unit | `tests/document/`, `tests/test_geometry.py`, … | Model, commands, codec, naming, settings — pure and fast |
| Widget | `tests/editor/`, `tests/capture/` | Tools turn input into the right commands; canvas paints what the document says |
| UX journeys | `tests/ux/` | Each `ux-contract.md` item, driven like a user, asserting on observable outcomes |
| Platform | `tests/platform/` | ctypes adapters with the OS boundary faked |
| Visual review | `uv run poe ux-snapshots`, `uv run poe ux-gallery` | Each journey step, and key screens in light and dark, as PNGs for a human eye |
| Physical smoke (exceptional) | `docs/design/ux-contract.md` § Manual checklist | Critical real-device gaps not reliably covered by automated evidence |
