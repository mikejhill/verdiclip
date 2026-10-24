# VerdiClip brand

<!-- markdownlint-disable MD033 -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="wordmark-dark.svg">
  <img src="wordmark-light.svg" alt="VerdiClip" width="320">
</picture>
<!-- markdownlint-enable MD033 -->

The revised mark retains the familiar green tile and white V. Mint capture
corners make its screenshot purpose visible; deeper emerald gives the white
glyph more contrast. Two corners preserve breathing room at tray sizes.

## Assets

| File | Use |
| --- | --- |
| `icon.svg` | Editable square master |
| `wordmark-light.svg`, `wordmark-dark.svg` | Outlined identity for either theme |
| `mark-light.svg`, `mark-dark.svg` | Single color marks without a tile |
| `icon-16.png` through `icon-512.png` | Transparent pixel exports |
| `wordmark-light.png`, `wordmark-dark.png` | Transparent 2x fallback exports |
| `verdiclip.ico` | Multi-size Windows icon for design handoff |
| `../../src/verdiclip/assets/brand.svg` | Packaged runtime copy of `icon.svg` |

## Design rules

- Emerald: `#087F64`; mint: `#9BE9C5`; V: `#FFFFFF`.
- Wordmark ink: `#172544` for light surfaces; `#EDF5FA` for dark surfaces.
- Keep 8 units of clear space around the 64-unit icon.
- Use the icon alone below a 160-pixel wordmark width; minimum icon size: 16px.
- Use the same full color tile in both themes; single color variants are for
  surfaces requiring one ink.
- Scale proportionally; do not stretch, rotate, add shadows, or recolor parts.
- Segoe UI Semibold lettering is outlined. No font files are distributed and
  the assets render without installed fonts.

SVG is authoritative. Edit it in a vector editor, copy `icon.svg` to the runtime
asset, and regenerate the named PNG dimensions with transparency. Wordmark PNGs
are twice the SVG's intrinsic size. ICO includes 16, 20, 24, 32, 48, 64, 128,
and 256px PNG frames. Check actual tray sizes and both background themes.

`IconFactory.brand()` loads the packaged SVG, allowing Qt to render at the
requested display size. Existing tray, editor, dialog, shortcut, and Explorer
icon consumers use that factory. The application's existing per-user ICO
writer still writes its requested 256px rendering; this change does not alter
shell registration or default-app behavior.
