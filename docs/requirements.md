# VerdiClip Requirements Specification

This document captures the complete set of functional and non-functional requirements for VerdiClip, organized by feature area. It serves as the authoritative reference for what the application must do and how it must behave.

---

## 1. Screenshot Capture

### 1.1 Capture Modes

| Mode | Default Hotkey | Description |
|------|----------------|-------------|
| Region | `PrtSc` | User draws a rectangle on a transparent overlay to select a screen area |
| Full Screen | `Ctrl+PrtSc` | Captures all monitors (or primary only, configurable) |
| Active Window | `Alt+PrtSc` | Captures the currently focused window |
| Window Picker | `Ctrl+Shift+PrtSc` | User clicks any visible window to capture it |
| Repeat Last | `Shift+PrtSc` | Repeats the most recent capture type and region |

### 1.2 Capture Behavior

- **Screen freeze**: When a capture is triggered, the full screen must be treated as "fixed" (frozen) until the capture completes. This allows the user to capture exactly what they see at the moment of triggering, even if on-screen elements are animating or changing.
- **DPI awareness**: Captures must produce correct results on displays using Windows scaling greater than 100%. The captured image must use physical pixel resolution, not logical coordinates.
- **After-capture action** (configurable): Open in editor (default), save to file, copy to clipboard, or show context menu.

### 1.3 Region Selection UX

- Full-screen transparent overlay dims the screen.
- Crosshair cursor with live pixel coordinates.
- Rubber-band rectangle with pixel dimensions shown.
- Magnifier loupe at cursor for precise selection.
- Press **Esc** to cancel.
- Arrow keys nudge the selection: 1 px normally, 10 px with Ctrl held.

### 1.4 Window Capture

- Uses Win32 API (`EnumWindows`, `GetWindowRect`, DWM extensions) to detect window boundaries.
- Configurable option to include or exclude window decorations.
- Window picker mode highlights windows on hover before selection.

---

## 2. Image Editor — Canvas

### 2.1 Viewport

- **Zoom**: Ctrl+scroll to zoom in/out. Range: 10% to 1600%.
- **Zoom target**: Zooming must center on the current cursor position. If the cursor is outside the viewport, zoom centers on the viewport center.
- **Zoom consistency**: Zoom in and zoom out must use a consistent center point. The viewport must not "jump around" between zoom levels.
- **Pan**: Middle-mouse-button drag or Space+drag.
- **Scroll**: Mouse wheel scrolls vertically. Shift+scroll scrolls horizontally at normal speed (not faster vertical scrolling).
- **Fit on open**: Canvas fits the captured image on initial open.

### 2.2 Status Bar

- **File name**: Displays the current file path after save.
- **Image dimensions**: Shows `W × H px`.
- **Zoom percentage**: Clickable button showing current zoom level (e.g., "100%").
  - Clicking opens a zoom slider popup (range 10%–400%) with sticky snap points at every 10%.
- **Zoom to Fit**: Icon button (⊞) to the right of the zoom display.

### 2.3 Undo / Redo

- Command pattern via QUndoStack.
- **Ctrl+Z** to undo, **Ctrl+Y** to redo.
- Unlimited undo depth during session.
- All operations are undoable: drawing, moving, resizing, cropping, deleting.
- Undoing a crop must restore all elements to their original positions.

### 2.4 Background and Borders

- The image sits on the canvas with a gray border frame.
- This border is a native rendering feature, not a selectable or movable object.
- Checkerboard pattern behind transparent areas.

---

## 3. Image Editor — Toolbar and Properties

### 3.1 Toolbar (Left Side, Vertical)

| Tool | Shortcut | Description |
|------|----------|-------------|
| Select | V | Select, move, and resize annotations |
| Crop | C | Crop the image to a selected area |
| Rectangle | R | Draw a rectangle with stroke and fill |
| Ellipse | E | Draw an ellipse with stroke and fill |
| Line | L | Draw a straight line |
| Arrow | A | Draw an arrow with configurable head |
| Text | T | Add text annotation with font selection |
| Number | N | Add auto-incrementing numbered marker |
| Highlight | H | Draw semi-transparent highlight rectangle |
| Obfuscate | O | Pixelate/blur a region |
| Freehand | F | Freehand pen drawing |

- Active tool is visually highlighted.
- Pressing **Esc** when a tool is active deactivates the tool (switches to Select).

### 3.2 Properties Panel (Top Bar)

- **Stroke color**: Color picker with recent colors palette.
- **Fill color**: Color picker with transparent fill option (default is transparent).
- **Stroke width**: Slider (1–20 px).
- **Font**: Font family dropdown (each entry rendered in its own font) and size.
- **Line caps**: Start/end caps (none, arrow, rounded).
- **Obfuscation strength**: Pixelation block size (for Obfuscate tool only).

### 3.3 Properties Behavior

- **Selected element sync**: When an element is selected, the properties panel updates to reflect that element's current values (stroke color, fill, width, font, etc.). Changes in the panel apply to the selected element.
- **Default toolbar**: When no element is selected, the properties panel shows and controls defaults for newly created elements.
- **Per-tool visibility**: The panel shows only properties relevant to the active tool (e.g., font only for Text/Number, caps only for Line/Arrow).

---

## 4. Image Editor — Tool Behaviors

### 4.1 Select Tool

- Click to select a single element.
- Click-and-drag on empty space to create a rubber-band selection for multiple elements.
- **Ctrl+A** selects all elements.
- Selected elements display resize handles.
- Selected elements can be **dragged** to new positions.
- **Arrow keys** nudge selected elements: 1 px normally, 10 px with Ctrl held.
- **Delete** key removes selected elements.
- **Esc** first deselects all. If nothing is selected, switches to Select tool.
- Handle anchor points show a cursor indicator (e.g., resize cursor) when hovered.
- The gray image border must not be selectable, draggable, or editable.

### 4.2 Rectangle and Ellipse Tools

- Click-and-drag to draw.
- Hold **Shift** to constrain: square (rectangle) or circle (ellipse).
- Stroke color, fill color, and stroke width apply.
- Resizable in both dimensions after creation via handles.

### 4.3 Line Tool

- Click-and-drag to draw a straight line.
- Hold **Shift** to snap to 45° angle intervals.
- Shift-snap applies during both creation and modification.
- Start and end points independently movable after creation.
- Configurable line caps (start/end).

### 4.4 Arrow Tool

- Click-and-drag to draw a line with an arrowhead.
- Hold **Shift** to snap to 45° angle intervals (creation and modification).
- **Arrowhead must remain pointy** regardless of stroke width. The shaft line must end at the arrowhead base, not the tip, to prevent the thick line from covering the point.
- Arrow head size scales proportionally with stroke width.
- Start and end points independently movable after creation.
- Configurable line caps.

### 4.5 Text Tool

- Click to place a text box with inline editing.
- Font family, size, bold, italic from properties panel.
- **Delete** and **Backspace** keys must work for character deletion within the text box.
- Text color controlled via stroke color.

### 4.6 Number Tool (Counters)

- Click to place a numbered marker (filled circle with centered number).
- Auto-incrementing counter: each new marker is one higher than the last inserted or changed value.
- **Editable values**: When selected, an editable field appears to change the number. Accepts **non-numeric values** as well.
- Changing one number has no impact on any others.
- If the last changed value is non-numeric, the next new counter starts at "1".
- **1:1 aspect ratio** maintained when resizing. Number always centered.
- Text box for editing must not be auto-focused; requires manual cursor focus. This ensures Delete key deletes the marker, not the text value.

### 4.7 Highlight Tool

- Click-and-drag to draw a semi-transparent filled rectangle.
- Default: yellow at 50% opacity.
- Resizable after creation.

### 4.8 Obfuscate Tool

- Click-and-drag to define a pixelation region.
- A **border must render** around the obfuscation rectangle during creation to show boundaries.
- **Drag past edges**: When the drag handle exceeds the image boundaries (top or left), only the corresponding edge of the box moves — not the entire box.
- Obfuscation elements are not bounded to image boundaries — they can extend beyond the image.
- Obfuscation elements must not disappear when included in a rubber-band selection.
- Resizable after creation.

### 4.9 Crop Tool

- Click-and-drag to define crop region.
- Releasing the mouse ends the selection (single-click-drag, not two-click).
- **Enter** or double-click to confirm crop.
- **Esc** to cancel.
- Crop is bounded by the image borders — cannot be used to expand the image.
- Cropping is **undoable**. Undoing a crop must restore the image and all elements to their exact original positions.
- Crop must not cause arrows or other elements to disappear.

### 4.10 Freehand Tool

- Smooth pen drawing with configurable color and width.
- Uses QPainterPath for smooth curves.

---

## 5. Image Editor — Element Manipulation

### 5.1 Resize Handles

- Rectangles, ellipses, obfuscation masks, and highlights are resizable in both dimensions.
- Lines and arrows have both start and end points independently movable.
- Counters maintain 1:1 aspect ratio when resizing.
- **Corners must remain sharp** when stroke width increases (especially for rectangles and arrows).

### 5.2 Copy and Paste

- **Ctrl+C** / **Ctrl+V**: Copy and paste annotation elements.
- **Ctrl+Shift+C**: Copy the full image to clipboard (does not conflict with element copy).
- Pasted elements are offset slightly from the original to indicate duplication.

### 5.3 Keyboard Shortcuts (Editor)

| Action | Shortcut |
|--------|----------|
| Undo | Ctrl+Z |
| Redo | Ctrl+Y |
| Delete Selected | Delete |
| Select All | Ctrl+A |
| Copy Elements | Ctrl+C |
| Paste Elements | Ctrl+V |
| Copy Image to Clipboard | Ctrl+Shift+C |
| Save | Ctrl+S |
| Save As | Ctrl+Shift+S |
| Open | Ctrl+O |
| Print | Ctrl+P |
| Close | Ctrl+W |
| Zoom In | Ctrl+= |
| Zoom Out | Ctrl+- |
| Zoom 100% | Ctrl+0 |
| Zoom to Fit | Ctrl+Shift+F |
| Nudge Selected (1 px) | Arrow Keys |
| Nudge Selected (10 px) | Ctrl+Arrow Keys |

### 5.4 Esc Key Behavior

1. If a toolbar widget (e.g., Width slider) is focused, forward Esc to the canvas.
2. If a crop region is active, cancel the crop.
3. If elements are selected, deselect all.
4. If nothing is selected and a non-Select tool is active, switch to Select tool.

---

## 6. Export

### 6.1 File Export

| Action | Trigger | Details |
|--------|---------|---------|
| Save | Ctrl+S / File menu | Format: PNG (default), JPG, BMP, GIF, TIFF. Quality slider for JPG. |
| Save As | Ctrl+Shift+S | Choose location and format |
| Auto-save | Configurable | Save immediately after capture with pattern-based filename |

- After saving, the file path appears in the editor title bar and status bar.

### 6.2 Auto-Save Filename Pattern

Configurable pattern with tokens:

| Token | Example Output |
|-------|---------------|
| `{datetime}` | `20260401_164800` |
| `{date}` | `20260401` |
| `{time}` | `164800` |
| `{counter}` | Incrementing number |
| `{title}` | `Screenshot` |

### 6.3 Clipboard Export

- **Ctrl+Shift+C**: Copies the flattened (rendered) image to the Windows clipboard.
- Flattening rasterizes all vector annotations onto the image.

### 6.4 Print

- **Ctrl+P**: Qt print dialog with preview.
- Supports any installed printer.

---

## 7. System Integration

### 7.1 System Tray

- Custom green "V" icon in the Windows notification area.
- **Left-click**: Show/hide the most recent editor window.
- **Right-click** context menu:
  - Capture Region / Capture Window / Capture Window (Select) / Capture Full Screen
  - Open Image...
  - Settings
  - About
  - Exit

### 7.2 Single Instance

- Only one instance of VerdiClip may run at a time.
- Launching a second instance focuses the existing instance.
- Enforced via QSharedMemory or named mutex.

### 7.3 Startup

- Optional "Run at Windows startup" via registry key (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`).
- Optional "Minimize to tray on startup".

### 7.4 Shutdown

- **SIGINT** (Ctrl+C in terminal) must shut down the application immediately without waiting for user interaction.
- Clean shutdown: stop hotkey listener, remove tray icon, close all editor windows.

### 7.5 Settings Dialog

- Accessible from tray menu → Settings.
- Modal to editors (cannot interact with editor windows while settings is open).
- Tabs: Capture, Save, Editor, Hotkeys, General.

### 7.6 About Dialog

- Version info, description, and Greenshot attribution.
- Accessible from tray menu → About.

---

## 8. Command-Line Interface

### 8.1 Subcommands

#### `capture` — Headless Screenshot

```
verdiclip capture <mode> [options]
```

Modes: `screen`, `region`, `window`

| Option | Description |
|--------|-------------|
| `-o, --output` | Output file path (auto-generates if omitted) |
| `--region` | Region coordinates `X,Y,W,H` (required for `region` mode) |
| `--monitor` | Monitor index, 1-based (for `screen` mode) |
| `--format` | Image format: `png`, `jpg`, `bmp`, `tiff` |
| `--quality` | JPEG quality 1–100 (default: 90) |
| `--delay` | Delay in seconds before capturing (default: 0) |
| `--clipboard` | Copy to clipboard instead of saving to file |

#### `open` — Open Image in Editor

```
verdiclip open <file>
```

Opens any supported image file in the editor.

#### `--version`

Prints the application version.

---

## 9. Configuration

### 9.1 Storage

- Location: `%APPDATA%\VerdiClip\config.json`
- Format: JSON with dot-notation access (e.g., `config.get("save.default_format")`)
- Auto-saves on change.
- Default values merged on load for forward compatibility.

### 9.2 Configuration Keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `capture.default_action` | string | `"editor"` | After-capture action: `editor`, `file`, `clipboard`, `menu` |
| `capture.include_cursor` | bool | `false` | Include mouse cursor in capture |
| `capture.region_magnifier` | bool | `true` | Show magnifier during region selection |
| `capture.window_decorations` | bool | `true` | Include window decorations |
| `save.default_directory` | string | `""` | Default save directory |
| `save.default_format` | string | `"png"` | Default file format |
| `save.jpg_quality` | int | `90` | JPEG quality (1–100) |
| `save.auto_save_enabled` | bool | `false` | Auto-save captures |
| `save.filename_pattern` | string | `"Screenshot_{datetime}"` | Auto-save filename pattern |
| `hotkeys.region` | string | `"print_screen"` | Region capture hotkey |
| `hotkeys.fullscreen` | string | `"ctrl+print_screen"` | Full screen capture hotkey |
| `hotkeys.window` | string | `"alt+print_screen"` | Active window capture hotkey |
| `hotkeys.window_picker` | string | `"ctrl+shift+print_screen"` | Window picker hotkey |
| `hotkeys.repeat` | string | `"shift+print_screen"` | Repeat last capture hotkey |
| `editor.default_stroke_color` | string | `"#ff0000"` | Default stroke color (hex) |
| `editor.default_fill_color` | string | `"#00000000"` | Default fill color (transparent) |
| `editor.default_stroke_width` | int | `2` | Default stroke width |
| `startup.run_at_login` | bool | `false` | Run at Windows startup |
| `startup.minimize_to_tray` | bool | `true` | Minimize to tray on startup |

---

## 10. Open Existing Images

- **File → Open** (Ctrl+O) loads any supported image file into the editor.
- Supported formats: PNG, JPG, BMP, GIF, TIFF, WEBP.
- Opens in the same editor with full annotation capabilities.
- Drag-and-drop onto the editor window.

---

## 11. Non-Functional Requirements

### 11.1 Performance Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| Region capture latency | < 150 ms | Time from hotkey to image in memory |
| Full-screen capture | < 200 ms | Time for mss capture of all monitors |
| Editor window open | < 500 ms | Time from capture to editor visible |
| Tool draw operation | < 16 ms (60 fps) | Time per paint event during annotation |
| Memory (idle, tray only) | < 50 MB | RSS after startup |
| Memory (editing 4K image) | < 300 MB | RSS with 4K image + 20 annotations |
| Save 4K PNG | < 1 s | Pillow save time |
| App startup to tray | < 2 s | Time from process start to tray icon |

### 11.2 Platform

- Windows 10 or later only.
- Python 3.12+.

### 11.3 Licensing and Attribution

- **License**: MIT.
- **Clean-room implementation**: No Greenshot code, icons, or assets are used.
- **Attribution**: README, ATTRIBUTION.md, and About dialog credit Greenshot as inspiration.
- **No trademark infringement**: The name "Greenshot" and its logo are not used in branding.

### 11.4 Dependencies

All dependencies must be corporate-friendly with strong community adoption:

| Package | License | Purpose |
|---------|---------|---------|
| PySide6 | LGPL-3.0 | GUI framework (Qt 6) |
| Pillow | HPND | Image manipulation |
| mss | MIT | Fast multi-screen capture |
| pynput | LGPL-3.0 | Global hotkey listener |

Dev-only dependencies (not shipped):

| Package | License | Purpose |
|---------|---------|---------|
| pytest | MIT | Testing framework |
| pytest-qt | MIT | Qt widget testing |
| pytest-benchmark | BSD-2 | Performance benchmarks |
| pytest-cov | MIT | Coverage reporting |
| ruff | MIT | Linting and formatting |
| pyinstaller | GPL-2.0+ | Executable packaging (build tool only) |

### 11.5 Code Quality

- Lint-clean with ruff.
- Type hints on all public APIs.
- Comprehensive test suite (unit, integration, performance).
- Conventional Commits for all git history.
