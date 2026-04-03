# VerdiClip Architecture

## Overview

VerdiClip is a screenshot and annotation tool built with Python and Qt (PySide6).
It runs as a system tray application on Windows, listening for global hotkeys
to trigger various screenshot capture modes.

## Component Diagram

```
┌──────────────────────────────────────────────────┐
│                   VerdiClipApp                    │
│  (app.py — QApplication, single-instance)        │
├──────────────┬───────────────┬───────────────────┤
│   TrayIcon   │ HotkeyManager │     Config        │
│  (tray/)     │  (hotkeys/)   │   (config.py)     │
├──────────────┴───────────────┴───────────────────┤
│                CLI (cli.py)                       │
│  Headless capture and open-in-editor commands     │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────┐
│         Capture Module           │
│  ┌──────────┬──────────────────┐ │
│  │  Screen  │   Region         │ │
│  │ (mss)    │  (overlay)       │ │
│  ├──────────┼──────────────────┤ │
│  │  Window  │  Window Picker   │ │
│  │ (Win32)  │  (interactive)   │ │
│  ├──────────┼──────────────────┤ │
│  │  Repeat  │                  │ │
│  │(last cap)│                  │ │
│  └──────────┴──────────────────┘ │
└──────────────────┬───────────────┘
                   │ QPixmap
                   ▼
┌──────────────────────────────┐
│       EditorWindow (window.py)       │
│  ┌──────────────────────────────┐    │
│  │    EditorCanvas (canvas.py)  │    │
│  │  (QGraphicsView/Scene)       │    │
│  ├──────────────────────────────┤    │
│  │  Toolbar │ Properties        │    │
│  ├──────────┴───────────────────┤    │
│  │      Tools (11)              │    │
│  │  Select, Crop, Rect,        │    │
│  │  Ellipse, Line, Arrow,      │    │
│  │  Text, Number, Highlight,   │    │
│  │  Obfuscate, Freehand        │    │
│  ├──────────────────────────────┤    │
│  │  EditorHistory (history.py)  │    │
│  ├──────────────────────────────┤    │
│  │  Serialization               │    │
│  │  (serialization.py)          │    │
│  └──────────────────────────────┘    │
└──────────────┬───────────────┘
               │ QPixmap (flattened)
               ▼
┌──────────────────────────────┐
│       Export Module           │
│  ┌──────────┬──────────────┐ │
│  │   File   │  Clipboard   │ │
│  │ (Pillow) │  (Qt)        │ │
│  ├──────────┼──────────────┤ │
│  │  Printer │  Auto-save   │ │
│  │  (Qt)    │  (config)    │ │
│  └──────────┴──────────────┘ │
└──────────────────────────────┘
```

## Technology Stack

| Component          | Technology          | License     |
|--------------------|---------------------|-------------|
| GUI Framework      | PySide6 (Qt 6)      | LGPL-3.0    |
| Image Processing   | Pillow              | HPND        |
| Screen Capture     | mss                 | MIT         |
| Global Hotkeys     | pynput              | LGPL-3.0    |
| Win32 API          | ctypes (stdlib)     | PSF         |
| Configuration      | json (stdlib)       | PSF         |
| Testing            | pytest + pytest-qt  | MIT         |

## Performance Design

### Screen Capture (mss)
- Direct memory-mapped screen capture
- No intermediate file I/O
- Multi-monitor stitching via virtual screen coordinates

### Editor Canvas (QGraphicsView)
- Hardware-accelerated rendering
- Only dirty regions are repainted
- Annotations are vector QGraphicsItems (not rasterized until export)

### Memory Management
- Screenshots stored as QPixmap (GPU-backed where available)
- Annotations are lightweight QGraphicsItem objects
- Flattening (rasterization) only happens at export time

## Configuration

Settings are persisted in `%APPDATA%\VerdiClip\config.json` using a
dot-notation access pattern:

```python
config.get("save.default_format")  # "png"
config.set("editor.default_stroke_width", 5)  # auto-saves
```

Default values are defined in `config.py:DEFAULT_CONFIG` and merged
with user settings on load, ensuring forward compatibility.

## Attribution

VerdiClip is inspired by Greenshot but is a clean-room implementation.
See ATTRIBUTION.md for details.
