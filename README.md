# VerdiClip

**A performant screenshot and annotation tool for Windows.**

> *"Verdi"* — Italian for *green*, a respectful nod to [Greenshot](https://getgreenshot.org/),
> the excellent open-source screenshot tool that inspired this project.

---

## ✨ Features

### Screenshot Capture
- **Region capture** — select any area of your screen with a precise crosshair overlay
- **Window capture** — capture the active window or click any window to grab it
- **Full-screen capture** — capture your entire display (multi-monitor supported)
- **Repeat last** — instantly retake the same type of screenshot

### Image Editor
- **Drawing tools** — rectangles, ellipses, lines, arrows, freehand pen
- **Annotations** — text labels, auto-numbered step markers, semi-transparent highlights
- **Obfuscation** — pixelate sensitive information
- **Crop** — trim your screenshot to the perfect size
- **Customizable** — stroke colors, fill colors, line widths, fonts
- **Undo/Redo** — unlimited history during your session

### Export
- **Save to file** — PNG, JPG, BMP, GIF, TIFF with quality controls
- **Copy to clipboard** — paste directly into any application
- **Print** — send to any printer with preview
- **Auto-save** — automatically save captures with configurable naming patterns

### System Integration
- **System tray** — runs quietly in the background
- **Global hotkeys** — capture screenshots from anywhere, fully configurable
- **Open existing images** — annotate any image file, not just screenshots
- **Lightweight** — minimal memory footprint when idle

---

## 🚀 Quick Start

### Prerequisites
- Windows 10 or later
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)

### Install from Source

```bash
git clone https://github.com/mikejhill/verdiclip.git
cd verdiclip
uv sync --all-extras
```

### Run

```bash
uv run verdiclip
```

### Default Hotkeys

| Action           | Hotkey              |
|------------------|---------------------|
| Region capture   | `PrtSc`             |
| Full screen      | `Ctrl+PrtSc`        |
| Active window    | `Alt+PrtSc`         |
| Window picker    | `Ctrl+Shift+PrtSc`  |
| Repeat last      | `Shift+PrtSc`       |

---

## ⚙️ Configuration

Settings are stored in `%APPDATA%\VerdiClip\config.json` and can be edited
through the **Settings** dialog (right-click the tray icon → Settings).

Configurable options include:
- Default save directory and image format
- Auto-save behavior and filename patterns
- Hotkey bindings
- Editor defaults (colors, stroke width)
- Startup behavior

---

## 🛠️ Development

### Setup

```bash
git clone https://github.com/mikejhill/verdiclip.git
cd verdiclip
uv sync --all-extras
```

### Run Tests

```bash
uv run pytest
```

### Run with Coverage

```bash
uv run pytest --cov=verdiclip --cov-report=html
```

### Lint

```bash
uv run ruff check src/ tests/
```

### Build Executable

```powershell
.\scripts\Build-Release.ps1
```

---

## 📁 Project Structure

```
verdiclip/
├── src/verdiclip/       # Application source code
│   ├── capture/         # Screenshot capture methods
│   ├── editor/          # Image editor and annotation tools
│   ├── export/          # File, clipboard, and print export
│   ├── hotkeys/         # Global hotkey management
│   ├── tray/            # System tray integration
│   └── ui/              # Settings and about dialogs
├── tests/               # Unit, integration, and performance tests
├── resources/           # Icons and default configuration
├── scripts/             # PowerShell build and setup scripts
└── docs/                # User and developer documentation
```

---

## 📄 License

VerdiClip is licensed under the [MIT License](LICENSE).

## 🙏 Attribution

VerdiClip is an independent, clean-room implementation inspired by
[Greenshot](https://getgreenshot.org/). No code, assets, or copyrighted
materials from Greenshot are used. See [ATTRIBUTION.md](ATTRIBUTION.md) for
details.