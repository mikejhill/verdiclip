# Documentation visuals

The editor, selection overlay, and Settings images are captures of the actual
current VerdiClip Qt widgets. They are generated on Windows in both themes;
no application chrome, controls, or results are painted onto screenshots.
The image being annotated is an original fictional itinerary, explicitly
marked as demo content. It contains no personal data or third-party photos.

Regenerate the screenshots from the repository root:

```powershell
uv run python scripts/ux_gallery.py --readme-dir docs/images
```

Review `docs/ux/gallery/` in both themes before committing. Screenshots are
1000×640 pixels or smaller. Keep PNGs lossless so labels remain readable.

`source/fieldnotes.svg` is the editable source of the demo content.
`source/workflow.svg` is the editable source of the capture → annotate → deliver
explanation. `workflow.png` is its static fallback for PyPI and other renderers
that do not support interactive diagrams or inline SVG. Its alt text carries
the same information when images cannot be loaded. There is no Mermaid diagram
in the package description; architecture diagrams in `docs/design/philosophy.md`
are plain text and remain readable without JavaScript.

The README uses absolute HTTPS links to repository-hosted PNGs so the wheel's
Markdown description renders on PyPI. Repository documentation uses local
assets where appropriate. Release notes can embed `editor.png` and link to the
same source; they should not embed machine-local paths or GitHub-only HTML.
The sources, generated diagrams, and screenshot example content are original
VerdiClip project material under the repository's MIT license.
