# VerdiClip User Guide

## Table of Contents

- [Getting Started](#getting-started)
- [Taking Screenshots](#taking-screenshots)
- [Using the Editor](#using-the-editor)
- [Saving and Exporting](#saving-and-exporting)
- [Configuration](#configuration)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Command-Line Interface](#command-line-interface)

---

## Getting Started

After launching VerdiClip, it minimizes to the **system tray** (notification area)
in the bottom-right corner of your taskbar. Look for the green "V" icon.

**Right-click** the tray icon to access the main menu:

- **Capture Region** — select an area of your screen
- **Capture Window** — capture the active window
- **Capture Window (Select)...** — click any window to capture it
- **Capture Full Screen** — capture everything
- **Open Image...** — open an existing image for editing
- **Settings** — configure VerdiClip
- **About** — version and attribution info
- **Exit** — close VerdiClip

## Taking Screenshots

### Region Capture (PrtSc)

1. Press **PrtSc** (or use the tray menu)
2. Your screen dims with a crosshair cursor
3. **Click and drag** to select the area you want
4. A magnifier shows a zoomed view for precision
5. Release the mouse button to capture

**Tips:**
- Press **Esc** to cancel
- The selection shows pixel dimensions as you drag

### Window Capture (Alt+PrtSc)

Press **Alt+PrtSc** to instantly capture the currently active window.

### Full Screen Capture (Ctrl+PrtSc)

Press **Ctrl+PrtSc** to capture your entire display (all monitors).

### Repeat Last (Shift+PrtSc)

Press **Shift+PrtSc** to repeat your most recent capture type.

## Using the Editor

After capturing, the image opens in the **VerdiClip Editor**.

### Toolbar (Left Side)

| Tool         | Shortcut | Description                               |
|-------------|----------|-------------------------------------------|
| Select       | V        | Select and move annotations               |
| Crop         | C        | Crop the image                            |
| Rectangle    | R        | Draw a rectangle                          |
| Ellipse      | E        | Draw an ellipse                           |
| Line         | L        | Draw a straight line                      |
| Arrow        | A        | Draw an arrow                             |
| Text         | T        | Add text annotation                       |
| Number       | N        | Add numbered step marker                  |
| Highlight    | H        | Highlight an area (semi-transparent)      |
| Obfuscate    | O        | Pixelate/blur sensitive content           |
| Freehand     | F        | Draw freehand                             |

### Properties Bar (Top)

- **Stroke color** — outline color for shapes and lines
- **Fill color** — interior color for shapes
- **Width** — stroke thickness (1–20 px)
- **Font** — font family and size for text tool
- **Obfuscation** — pixelation strength

### Drawing Tips

- Hold **Shift** while drawing rectangles for perfect **squares**
- Hold **Shift** while drawing ellipses for perfect **circles**
- Hold **Shift** while drawing lines to snap to **15° angles**
- **Middle-click drag** to pan the canvas
- **Ctrl+scroll** to zoom in/out
- **Ctrl+Z** to undo, **Ctrl+Y** to redo

## Saving and Exporting

| Action              | Shortcut      | Description                          |
|--------------------|---------------|--------------------------------------|
| Save               | Ctrl+S        | Save to file                         |
| Save As            | Ctrl+Shift+S  | Choose location and format           |
| Copy to Clipboard  | Ctrl+C        | Paste into any application           |
| Print              | Ctrl+P        | Print with preview                   |

### Supported Formats

PNG, JPG, BMP, GIF, TIFF

### Auto-Save

Enable auto-save in Settings to automatically save every capture. Configure
the save directory and filename pattern using tokens:

- `{datetime}` — full timestamp (20260401_164800)
- `{date}` — date only (20260401)
- `{time}` — time only (164800)
- `{counter}` — incrementing number
- `{title}` — "Screenshot"

## Configuration

Access settings via the tray menu → **Settings**.

Settings are stored in: `%APPDATA%\VerdiClip\config.json`

### Tabs

- **Capture** — default action after capture, cursor inclusion, magnifier
- **Save** — directory, format, quality, auto-save, filename pattern
- **Editor** — default stroke width, font
- **Hotkeys** — customize keyboard shortcuts
- **General** — startup behavior, minimize to tray

## Keyboard Shortcuts

### Global (System-Wide)

| Action           | Default Hotkey       |
|------------------|---------------------|
| Region capture   | PrtSc               |
| Full screen      | Ctrl+PrtSc          |
| Active window    | Alt+PrtSc           |
| Window picker    | Ctrl+Shift+PrtSc    |
| Repeat last      | Shift+PrtSc         |

### Editor

| Action          | Shortcut      |
|-----------------|---------------|
| Undo            | Ctrl+Z        |
| Redo            | Ctrl+Y        |
| Delete Selected | Delete        |
| Save            | Ctrl+S        |
| Save As         | Ctrl+Shift+S  |
| Copy            | Ctrl+C        |
| Print           | Ctrl+P        |
| Open            | Ctrl+O        |
| Close           | Ctrl+W        |
| Zoom In         | Ctrl+=        |
| Zoom Out        | Ctrl+-        |
| Zoom 100%       | Ctrl+0        |
| Zoom to Fit     | Ctrl+Shift+F  |

## Command-Line Interface

VerdiClip provides a CLI for headless screenshot capture and opening images
in the editor without launching the system tray application.

### Subcommands

#### `capture` — Take a Screenshot

```bash
uv run verdiclip capture <mode> [options]
```

**Modes:** `screen`, `region`, `window`

**Options:**

| Option         | Description                                              |
|----------------|----------------------------------------------------------|
| `-o, --output` | Output file path (auto-generates if omitted)             |
| `--region`     | Region coordinates `X,Y,W,H` (required for `region`)    |
| `--monitor`    | Monitor index, 1-based (for `screen` mode)               |
| `--format`     | Image format: `png`, `jpg`, `bmp`, `tiff`                |
| `--quality`    | JPEG quality 1–100 (default: 90)                         |
| `--delay`      | Delay in seconds before capturing (default: 0)           |
| `--clipboard`  | Copy to clipboard instead of saving to file              |

**Examples:**

```bash
# Capture full screen
uv run verdiclip capture screen -o screenshot.png

# Capture specific monitor
uv run verdiclip capture screen --monitor 1 -o monitor1.png

# Capture region (coordinates)
uv run verdiclip capture region --region 100,100,800,600 -o region.png

# Capture active window
uv run verdiclip capture window -o window.png

# Copy to clipboard instead of file
uv run verdiclip capture screen --clipboard

# With delay
uv run verdiclip capture screen --delay 3 -o delayed.png
```

#### `open` — Open an Image in the Editor

```bash
uv run verdiclip open <file>
```

**Example:**

```bash
uv run verdiclip open photo.png
```

### Version

```bash
uv run verdiclip --version
```
