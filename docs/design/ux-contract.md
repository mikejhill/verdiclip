# VerdiClip UX Contract

Every interaction VerdiClip supports, written as observable behavior. Each item
has an ID; the journey test that proves it carries the same ID in its
docstring (`grep -r "UX-EDT-07" tests/ux`). An item without a passing journey
test is not done.

Conventions: **Given / When / Then** describes what the user does and sees.
"Observable" means pixels on screen, clipboard contents, files on disk,
window state, or text the user can read — never internal state.

---

## Global principles (apply everywhere)

| ID | Rule |
| --- | --- |
| UX-G-01 | `Esc` backs out exactly one level and never discards committed work. |
| UX-G-02 | Every menu action shows its shortcut; every toolbar button has a tooltip naming the action and its shortcut. |
| UX-G-03 | Every document change is undoable with `Ctrl+Z` and redoable with `Ctrl+Y` / `Ctrl+Shift+Z`. While a text editor is open, these undo typing first; with nothing typed they undo the document (e.g. the box just drawn). |
| UX-G-04 | The canvas and every exported image are pixel-identical for the image area. |
| UX-G-05 | Failures the user can act on (hotkey conflict, save failure) are shown to the user, not only logged. |
| UX-G-06 | Closing an editor whose current state has not been delivered (copied, saved, printed) asks: Save / Discard / Cancel. |
| UX-G-07 | Window chrome never jumps: switching tools or selecting items never changes the toolbar height or moves the canvas. |
| UX-G-08 | Light, dark, or match-Windows theme applies to every window immediately, including toolbar icons. |

## Capture

| ID | Given / When | Then |
| --- | --- | --- |
| UX-CAP-01 | User presses the region hotkey | Within 150 ms every monitor shows a frozen, dimmed copy of itself with a crosshair cursor. The guide lines, magnifier, and the highlight of the window under the cursor appear immediately, without touching the mouse. Moving content underneath does not change the overlay. |
| UX-CAP-02 | User drags a rectangle on the overlay | The rectangle shows undimmed, with a border and a live `W × H` label in physical pixels. |
| UX-CAP-03 | User releases after dragging ≥ 3 px in both directions | The overlay closes and the selected pixels — exactly the frozen pixels, at physical resolution — go to the after-capture action. |
| UX-CAP-04 | User hovers a window on the overlay without dragging | The window under the cursor is highlighted with its title shown; a click (no drag) captures that window's frozen pixels. |
| UX-CAP-05 | User presses `Esc` or right-clicks on the overlay | All overlays close; nothing is captured; no editor opens. |
| UX-CAP-06 | During selection the user presses arrow keys | The cursor moves 1 px (10 px with `Ctrl`); a magnifier near the cursor shows the pixels at 4× zoom with a center crosshair. |
| UX-CAP-07 | User presses the full-screen hotkey | All monitors are captured immediately without an overlay. |
| UX-CAP-08 | User presses the active-window hotkey | The foreground window is captured immediately (DWM bounds, no invisible borders). |
| UX-CAP-09 | User presses the repeat hotkey after a region capture | The same screen region is captured again immediately, without an overlay. After a window/full capture, that capture kind is repeated. With no prior capture, it behaves like the region hotkey. |
| UX-CAP-10 | A configured hotkey is taken by another program | A tray notification names the hotkey and says it is unavailable; the tray menu entry still works. |
| UX-CAP-11 | A capture finishes with *Open in the editor* on (default) | An editor opens within 300 ms, focused, showing the image at 100% (or fit-to-window if larger than the window), with the Select tool active. |
| UX-CAP-12 | *Copy to the clipboard* is on | The image is on the clipboard and a tray notification confirms it. |
| UX-CAP-13 | *Save to the output folder* is on | The image is saved using the filename pattern; the notification shows the path and clicking it opens the folder. If the editor also opens, it is already linked to that file, so `Ctrl+S` overwrites it. |
| UX-CAP-14 | Any combination of the three after-capture actions | All chosen actions run for every capture. Settings won't accept turning all three off. |

## Editor: canvas and navigation

| ID | Given / When | Then |
| --- | --- | --- |
| UX-NAV-01 | `Ctrl+wheel` over the canvas | Zooms in/out keeping the image point under the cursor fixed (whenever the scroll range allows; a small image stays centered). Range 10%–1600%. |
| UX-NAV-02 | `Ctrl+=` / `Ctrl+-` / `Ctrl+0` / `Ctrl+Shift+F` | Zoom in / out around the viewport center / 100% / fit. |
| UX-NAV-03 | Wheel / `Shift+wheel` | Scroll vertically / horizontally at the same speed. |
| UX-NAV-04 | Middle-drag, or hold `Space` and left-drag | Pans the view. |
| UX-NAV-05 | Status bar | Shows image size `W × H`, zoom %, cursor position in image pixels, and the current tool's one-line hint. |
| UX-NAV-06 | Image has transparency | A checkerboard shows through transparent pixels on screen; exports keep the transparency. |

## Editor: tools

All drawing tools: drag to create; the new annotation is selected on release
so it can be adjusted immediately; the tool stays active for the next mark.

| ID | Given / When | Then |
| --- | --- | --- |
| UX-TL-01 | Tool shortcuts `V C R E L A T N H O F` | Select, Crop, Rectangle, Ellipse, Line, Arrow, Text, Counter, Highlight, Obfuscate, Freehand become active and the toolbar shows which. |
| UX-TL-02 | Rectangle / Ellipse drag, `Shift` held | A square / circle. Rectangle corners are sharp (miter joins) at every stroke width. |
| UX-TL-03 | Line / Arrow drag, `Shift` held | Angle snaps to 45° steps, during creation and when dragging an endpoint later. |
| UX-TL-04 | Arrow at any width | The head is a pointed triangle sized proportionally to the width; the shaft never covers the tip. |
| UX-TL-05 | Text: click | A text box opens for typing at the click point with the current font and stroke color. `Backspace`/`Delete` edit characters. `Esc` or clicking elsewhere commits; empty text is discarded. Double-click existing text to edit it. |
| UX-TL-06 | Counter: click | A filled circle with the next number. Next number = last placed/edited value + 1; if that value is not a number, start at 1. |
| UX-TL-07 | Counter: double-click an existing counter | An inline editor opens to change its label (any text). Other counters are unaffected. |
| UX-TL-08 | Highlight drag | A yellow, 50%-opacity rectangle that multiplies with the image (text underneath stays readable). |
| UX-TL-09 | Obfuscate drag | The region is pixelated from the image beneath it; a dashed border shows while dragging and while selected. Moving, resizing, or cropping keeps pixelating whatever image is beneath. Exported pixels inside the region never contain the original detail. |
| UX-TL-10 | Freehand drag | A smooth stroke following the cursor. |
| UX-TL-11 | Crop drag | A crop frame appears with the outside dimmed; the frame is clamped to the image. `Enter` or double-click applies; `Esc` cancels. Crop is non-destructive: undo restores the full image and every annotation at its exact original position. |
| UX-TL-12 | Drag shorter than 3 px with a shape tool | Nothing is created. |
| UX-TL-13 | Draw a rectangle or ellipse; or double-click inside one (even a hollow one); or press `Enter`/`F2` with one selected | An editor opens inside the shape (automatically right after drawing, with a "Type a label" hint); typed text is centered, wraps to the shape's width, and uses its line color and font. `Esc` or clicking elsewhere commits; clearing the text removes the label. One undo step. |

## Editor: selection and manipulation

| ID | Given / When | Then |
| --- | --- | --- |
| UX-SEL-01 | Click an annotation with Select | It is selected and shows handles; the style bar shows its style. |
| UX-SEL-02 | Drag on empty canvas with Select | A rubber band selects every annotation it intersects. `Shift+click` toggles membership. `Ctrl+A` selects all. |
| UX-SEL-03 | Drag a selected annotation | All selected annotations move together; one undo step. |
| UX-SEL-04 | Drag a handle | Rect-like shapes resize from that edge/corner; line/arrow endpoints move independently; counters keep a 1:1 aspect. Hovering a handle shows the matching resize cursor. |
| UX-SEL-05 | Arrow keys with a selection | Nudge 1 px (10 px with `Ctrl`). Consecutive nudges of the same selection coalesce into one undo step. |
| UX-SEL-06 | `Delete` / `Backspace` with a selection (not editing text) | Selected annotations are removed (undoable). |
| UX-SEL-07 | Change stroke/fill/width/font with a selection | The selected annotations change (one undo step) and the value becomes the default for new annotations. With no selection, only the default changes. |
| UX-SEL-08 | `Ctrl+C` then `Ctrl+V` with a selection | Copies of the annotations appear offset by 10 px and become the selection. Works between editor windows. |
| UX-SEL-09 | `Esc` | Order: finish text or label editing → cancel crop frame → clear selection → switch to Select tool. Focus in the style bar is returned to the canvas first. |
| UX-SEL-10 | `Ctrl+]` / `Ctrl+[` | Bring selected annotations forward / send backward. |
| UX-SEL-11 | Change a tool's color, fill, width, or font (directly or via a selected item) | The choice is remembered for that tool and used by every later editor, including after restarting. Changing editor defaults in Settings resets the remembered choices. |

## Delivery

| ID | Given / When | Then |
| --- | --- | --- |
| UX-OUT-01 | `Ctrl+Shift+C`, the toolbar "Copy image" button, or `Ctrl+C` with nothing selected | The flattened, cropped image is on the clipboard; status bar confirms. |
| UX-OUT-02 | `Ctrl+S` on a never-saved document | Saves to the default directory with the filename pattern (no dialog); the title and status bar show the path. Subsequent `Ctrl+S` overwrites that file. |
| UX-OUT-03 | `Ctrl+Shift+S` | Save As dialog in the last-used directory, format chosen by extension (PNG, JPG, BMP, GIF, TIFF, WEBP). |
| UX-OUT-04 | Save fails (permission, disk) | A message box explains the failure and the editor stays open with the work intact. |
| UX-OUT-05 | `Ctrl+P` | Print dialog; the image is scaled to fit the page, centered, orientation chosen by aspect. |
| UX-OUT-06 | `Ctrl+O` or drop an image file on an editor | Opens the image in a new editor. |
| UX-OUT-07 | `Enter` in the editor with Select active and no selection | Copies the image to the clipboard and closes the editor (the "quick path"). With one rectangle, ellipse, text, or counter selected, `Enter` edits its text instead (UX-TL-13). |

## Tray and settings

| ID | Given / When | Then |
| --- | --- | --- |
| UX-TRY-01 | App starts | A tray icon appears within 2 s; a second launch shows a notification in the running instance instead of starting another. |
| UX-TRY-02 | Left-click tray icon | Starts a region capture. |
| UX-TRY-03 | Right-click tray icon | Menu: capture actions (with hotkeys), Open Image…, Settings…, About, Exit. |
| UX-TRY-06 | `Ctrl+,` or File → Settings… in an editor | Opens the same Settings dialog as the tray. |
| UX-TRY-04 | Settings saved | Hotkeys re-register immediately and menu labels update; conflicts reported per UX-CAP-10. Every visible setting changes behavior. |
| UX-TRY-07 | Settings → General → Image files | "Show VerdiClip in Open with" registers VerdiClip for PNG, JPEG, BMP, GIF, TIFF, and WebP under the current user (no admin prompt), so Explorer's "Open with" lists it by name and icon; opening a file there opens it in an editor of the running instance. Unchecking removes only VerdiClip's entries. "Make VerdiClip the default image app…" turns that on and opens Windows' Default apps page for VerdiClip, where the user confirms; Windows does not let apps set their own default. |
| UX-TRY-05 | Ctrl+C in the launching terminal / Exit | App quits within 1 s; open editors with undelivered work prompt per UX-G-06 (Exit only). |

---

## Manual checklist (run before each release)

Automation cannot press the physical `PrtSc` key system-wide or attach a
second monitor at a different DPI. Check these by hand on Windows 11:

- [ ] `PrtSc`, `Ctrl+PrtSc`, `Alt+PrtSc`, `Shift+PrtSc` each trigger only their own action, and Windows' Snipping Tool does not also open (or the conflict notification appears if Windows owns `PrtSc`).
- [ ] Two monitors at 100% and 150% scaling: overlay covers both, selection on each yields physical-resolution pixels matching what was on screen.
- [ ] Monitor positioned left of / above the primary (negative coordinates).
- [ ] Window hover highlight on a maximized window, a snapped window, and a window partly off-screen.
- [ ] Clipboard paste into Paint, Word, Slack, and a browser.
- [ ] Print preview to "Microsoft Print to PDF".
- [ ] Settings → General → Image files: VerdiClip appears in Explorer's "Open with" for a `.png` with its name and icon, opens the file in the running instance, and "Make default…" lands on VerdiClip's Default apps page.
- [ ] Settings → General → Theme: Light, Dark, and Match Windows each restyle open editors (menus, toolbars, tool icons) without restarting.
