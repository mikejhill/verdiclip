# VerdiClip Developer Guide

## Prerequisites

- Windows 10 or later
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

## Setup

```bash
git clone https://github.com/mikejhill/verdiclip.git
cd verdiclip

# Option A: Use the setup script
.\scripts\Setup-DevEnvironment.ps1

# Option B: Manual setup
uv sync --all-extras
```

## Project Structure

```
src/verdiclip/
├── __init__.py          # Package metadata (__version__, __app_name__)
├── __main__.py          # Entry point, logging setup
├── app.py               # QApplication, single-instance, startup
├── config.py            # JSON config with dot-notation access
├── cli.py               # Command-line interface
├── capture/             # Screenshot capture methods
│   ├── screen.py        # Full-screen via mss
│   ├── region.py        # Region selection overlay
│   ├── window.py        # Window capture via Win32 API
│   ├── window_picker.py # Interactive window selection
│   └── repeat.py        # Repeat last capture
├── editor/              # Image editor
│   ├── canvas.py        # QGraphicsView canvas + EditorWindow
│   ├── toolbar.py       # Tool selection toolbar
│   ├── properties.py    # Color/stroke/font properties panel
│   ├── history.py       # QUndoStack undo/redo
│   └── tools/           # Individual drawing/annotation tools
├── export/              # Export functionality
│   ├── file_export.py   # Save to file (multi-format)
│   ├── clipboard.py     # Copy to clipboard
│   └── printer.py       # Print with preview
├── hotkeys/             # Global hotkey listener (pynput)
│   └── manager.py
├── tray/                # System tray icon
│   └── icon.py
└── ui/                  # UI dialogs
    ├── settings_dialog.py
    └── about_dialog.py
```

## Running

```bash
# Run the application
uv run verdiclip

# Run as module
uv run python -m verdiclip
```

## Testing

```bash
# Run all tests
uv run pytest

# Run with verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/unit/test_config.py

# Run with coverage
uv run pytest --cov=verdiclip --cov-report=html

# Run performance benchmarks
uv run pytest tests/performance/ --benchmark-only
```

## Linting

```bash
# Check for issues
uv run ruff check src/ tests/

# Auto-fix issues
uv run ruff check src/ tests/ --fix
```

## Type Checking

[ty](https://docs.astral.sh/ty/) (by Astral, the same team behind ruff) is used for static type analysis:

```bash
uv run ty check src/
```

Configuration is in `pyproject.toml` under `[tool.ty]`. Type checking runs against `src/` only (tests are excluded).

## Architecture

### Key Design Decisions

1. **PySide6 (Qt 6)** over PyQt6 — LGPL license is more corporate-friendly
2. **mss** for screen capture — faster than PIL.ImageGrab on multi-monitor
3. **QGraphicsView/Scene** for editor — hardware-accelerated, supports complex transformations
4. **QUndoStack** for undo/redo — Qt's built-in command pattern implementation
5. **pynput** for global hotkeys — works across all Windows applications
6. **JSON config** — human-readable, no external dependencies

### Data Flow

```
Hotkey/Tray → Capture → QPixmap → EditorWindow → Tools/Canvas → Export
                                        ↑
                                  Open File (QPixmap)
```

### Adding a New Tool

1. Create `src/verdiclip/editor/tools/your_tool.py`
2. Extend `BaseTool` and implement `mouse_press`, `mouse_move`, `mouse_release`
3. Add a `ToolType` entry in `toolbar.py`
4. Register the tool in `EditorWindow._register_tools()`
5. Add tests in `tests/unit/test_editor_tools.py`

## Building for Distribution

```powershell
# Build a standalone executable
.\scripts\Build-Release.ps1

# Build a single-file executable
.\scripts\Build-Release.ps1 -OneFile

# Clean and rebuild
.\scripts\Build-Release.ps1 -Clean -OneFile
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes (keep commits small and focused)
4. Run tests: `uv run pytest`
5. Run linter: `uv run ruff check src/ tests/`
6. Run type checker: `uv run ty check src/`
7. Submit a pull request
