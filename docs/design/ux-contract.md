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
| UX-G-01 | `Esc` backs out exactly one level toward rest and never discards committed work; `Enter` moves one step forward. The levels are defined in [Keyboard model](#keyboard-model). |
| UX-G-02 | Every menu action shows its shortcut; every toolbar button has a tooltip naming the action and its shortcut. |
| UX-G-03 | Every document change is undoable with `Ctrl+Z` and redoable with `Ctrl+Y` / `Ctrl+Shift+Z`. While a text editor is open, these undo typing first; with nothing typed they undo the document (e.g. the box just drawn). |
| UX-G-04 | The canvas and every exported image are pixel-identical for the image area. |
| UX-G-05 | Failures the user can act on (hotkey conflict, save failure) are shown to the user, not only logged. |
| UX-G-06 | Closing an editor whose current image hasn't been saved to a file or copied to the clipboard asks: **Save** (`S`, `Enter`) / **Don't save** (`N`) / **Cancel** (`Esc`). The question matches what would be lost: "Save changes to “<file>”" for an image opened from or saved to a file, "has changed since you copied it" for a copied screenshot with newer edits, and "hasn't been saved or copied" otherwise. This includes a fresh capture with no edits (unless an after-capture action already saved or copied it); an image opened from a file starts as saved. The prompt can be turned off in Settings → Editor (on by default) and the change applies to open editors immediately. |
| UX-G-07 | Window chrome never jumps: switching tools or selecting items never changes the toolbar height or moves the canvas. |
| UX-G-08 | Light, dark, or match-Windows theme applies to every window immediately, including toolbar icons. Controls stay legible after any switch (checked boxes show the accent fill in either mode), including when Windows changes mode under match-Windows. |
| UX-G-09 | A toolbar too long for the window ends in a `»` button; clicking it shows every hidden control in a panel over the canvas, and clicking it again hides them. |
| UX-G-10 | No dialog blocks a capture: while any VerdiClip dialog is open (Settings, About, a message, a file picker), capture hotkeys still work and the overlay takes the mouse and keyboard normally. A dialog opened from an editor blocks only that editor; one opened from the tray blocks nothing. |

## Keyboard model

The editor is always in one of these levels, from the most engaged to **rest**
(the Select tool with nothing selected). Every tool follows the same ladder, so
the keys mean the same thing everywhere.

| Level | You are… | `Esc` | `Enter` |
| --- | --- | --- | --- |
| 1. Typing | In a text, box-label, or counter editor | Finish typing. The item stays, selected, and the tool stays active for the next one. | New line in text and box labels; finishes a counter label. |
| 2. Gesture | Mid-drag, or holding an unapplied crop frame | Abandon it: an unfinished shape or crop frame is discarded, a rubber band selects nothing, and a move or resize already applied stays where it is (`Ctrl+Z` reverts it). | Apply the crop frame. |
| 3. Drawing tool | Any tool other than Select, between gestures | Return to rest: switch to Select and clear the selection, in one press. | Edit the selected item's text, if it has any. |
| 4. Selection | Select tool with something selected | Clear the selection. | Edit the selected item's text, if it has any. |
| Rest | Select tool, nothing selected | Nothing; `Esc` never closes the editor. | Copy the image and close the editor. |

Consequences:

- From anywhere, at most two presses of `Esc` reach rest: one to finish typing
  or abandon the gesture, one to leave the tool or selection.
- Choosing a tool (shortcut or toolbar) never changes the selection. Only
  clicking, `Esc`, `Ctrl+A`, and creating or deleting items do.
- Focus in the style bar is returned to the canvas by the first `Esc`, before
  any level is left.

## Capture

| ID | Given / When | Then |
| --- | --- | --- |
| UX-CAP-01 | User presses the region hotkey | Within 150 ms every monitor shows a frozen, dimmed copy of itself with a crosshair cursor. The guide lines, magnifier, and the highlight of the window under the cursor appear immediately, without touching the mouse. Moving content underneath does not change the overlay. |
| UX-CAP-02 | User drags a rectangle on the overlay | The rectangle shows undimmed, with a border and a live `W × H` label in physical pixels. A drag can cross onto other monitors (including ones with a different scale); the rectangle continues across them and captures every monitor it covers. |
| UX-CAP-03 | User releases after dragging ≥ 3 px in both directions | The overlay closes and the selected pixels — exactly the frozen pixels, at physical resolution — go to the after-capture action. |
| UX-CAP-04 | User hovers a window on the overlay without dragging | The window under the cursor is highlighted with its title shown — on every monitor it spans, not just the one with the cursor; a click (no drag) captures that window's frozen pixels. |
| UX-CAP-05 | User presses `Esc` or right-clicks on the overlay | All overlays close; nothing is captured; no editor opens. |
| UX-CAP-06 | During selection the user presses arrow keys | The cursor moves 1 px (10 px with `Ctrl`); a magnifier near the cursor shows the pixels at 4× zoom. Its reticle crosses the whole magnifier in bands exactly one image pixel wide, centered on the pixel under the cursor, which is left unobscured and outlined, so the cursor can be lined up with on-screen edges to the pixel. The coordinates shown are that pixel's. |
| UX-CAP-07 | User presses the full-screen hotkey | The monitor under the cursor is captured immediately, at physical resolution, without an overlay. Other monitors are not included. |
| UX-CAP-08 | User presses the active-window hotkey | The foreground window is captured immediately (DWM bounds, no invisible borders). |
| UX-CAP-09 | User presses the repeat hotkey after a region capture | The same screen region is captured again immediately, without an overlay. After a window/full capture, that capture kind is repeated (the full-screen kind on the monitor now under the cursor). With no prior capture, it behaves like the region hotkey. |
| UX-CAP-10 | A configured hotkey is taken by another program | A tray notification names the hotkey and says it is unavailable; the tray menu entry still works. |
| UX-CAP-11 | A capture finishes with *Open in the editor* on (default) | An editor opens within 300 ms, focused, showing the image at 100% (or fit-to-window if larger than the window), with the Select tool active. |
| UX-CAP-12 | *Copy to the clipboard* is on | The image is on the clipboard and a tray notification confirms it. |
| UX-CAP-13 | *Save to the output folder* is on | The image is saved using the filename pattern; the notification shows the path and clicking it opens the folder. If the editor also opens, it is already linked to that file, so `Ctrl+S` overwrites it. |
| UX-CAP-14 | Any combination of the three after-capture actions | All chosen actions run for every capture. Settings won't accept turning all three off. |
| UX-CAP-15 | During selection the user presses `Enter` | The first `Enter` starts a selection at the cursor, as a mouse press would; the arrow keys (or the mouse) then extend it. The second `Enter` — or a left click — finishes it exactly like releasing the mouse at that point: a region, or — without a ≥ 3 px drag — the window or monitor under the cursor. `Enter` acts at the cursor on whichever monitor it is on, not only the one it started on. |

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
| UX-TL-05 | Text: click | A text box opens for typing at the click point (a click outside the image starts at the nearest image edge) with the current font and stroke color, on a white background by default. The box auto-fits: it grows and shrinks with the text and its font. `Backspace`/`Delete` edit characters. `Esc` or clicking elsewhere commits; empty text is discarded. Double-click existing text to edit it. |
| UX-TL-14 | Text: drag | A fixed-size text box of the dragged size, clamped to the image, opens for typing with no placeholder hint; text wraps inside it. `Esc` or clicking elsewhere finishes typing and keeps the box, even when empty. Dragging a handle of selected text also fixes its size. With fixed text selected, the style bar offers horizontal (left, center, right) and vertical (top, middle, bottom) alignment, and **Auto-fit** sizes the box back to its text. |
| UX-TL-15 | Text outline and shadows | Text boxes take an outline color (none by default) and width. Text boxes and rectangles have a **Shadow** toggle that draws a soft drop shadow below and to the right; text with no background or outline shadows its letters. |
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
| UX-SEL-09 | `Esc` | Steps down the [Keyboard model](#keyboard-model): finish typing → abandon the gesture or crop frame → leave a drawing tool for Select and clear the selection together → in Select, clear the selection. Focus in the style bar is returned to the canvas first. |
| UX-SEL-10 | `Ctrl+]` / `Ctrl+[` | Bring selected annotations forward / send backward. |
| UX-SEL-11 | Change a tool's color, fill, width, or font (directly or via a selected item) | The choice is remembered for that tool and used by every later editor, including after restarting. Changing editor defaults in Settings resets the remembered choices. |

## Editor: image

| ID | Given / When | Then |
| --- | --- | --- |
| UX-IMG-01 | `Ctrl+R` / `Ctrl+Shift+R`, or Image → Rotate right / left | The image turns a quarter turn; every annotation and the crop turn with it, text stays upright, the selection is kept, and an open label editor is committed first. The canvas shows the new pixels immediately and the status bar shows the new size. One undo step restores the exact previous state. |
| UX-IMG-02 | `Ctrl+Shift+H` / `Ctrl+Shift+V`, Image → Flip, or the toolbar's Image button | The image mirrors with annotations (arrowheads stay at their ends). The toolbar Image button opens a menu listing rotate, flip, and resize with their shortcuts in one click. |
| UX-IMG-03 | `Ctrl+Alt+I` or Image → Resize… | A dialog opens at the visible (cropped) size with the width selected. Width, height, and percent stay in step while "Keep aspect ratio" is ticked (the default); unticking lets the sides differ, and ticking again re-links the height. OK scales the visible area to exactly that size, scaling stroke widths, fonts, and counters with it; Cancel, or OK with the size unchanged, does nothing. |

## Delivery

| ID | Given / When | Then |
| --- | --- | --- |
| UX-OUT-01 | `Ctrl+Shift+C`, the toolbar "Copy image" button, or `Ctrl+C` with nothing selected | The flattened, cropped image is on the clipboard; status bar confirms. |
| UX-OUT-02 | `Ctrl+S` on a never-saved document | Saves to the default directory with the filename pattern (no dialog); the title and status bar show the path. Subsequent `Ctrl+S` overwrites that file. The default pattern `{date}_{time}_{title}` names a window capture after its window (title cut to 80 characters, Windows-forbidden characters replaced); a pattern without spaces yields a name without spaces (title whitespace becomes `_`, collisions add `_2`); repeating a clicked-window capture keeps that title. Settings still holding an earlier default (`Screenshot {date} {time}` or `{date} {time} {title}`) are moved to the new default. |
| UX-OUT-03 | `Ctrl+Shift+S` | Save As dialog in the last-used directory, format chosen by extension (PNG, JPG, BMP, GIF, TIFF, WEBP). |
| UX-OUT-04 | Save fails (permission, disk) | A message box explains the failure and the editor stays open with the work intact. |
| UX-OUT-05 | `Ctrl+P` | Print dialog; the image is scaled to fit the page, centered, orientation chosen by aspect. |
| UX-OUT-06 | `Ctrl+O` or drop an image file on an editor | Opens the image in a new editor. |
| UX-OUT-07 | `Enter` in the editor with Select active and no selection | Copies the image to the clipboard and closes the editor (the "quick path"). With one rectangle, ellipse, text, or counter selected, `Enter` edits its text instead (UX-TL-13). |

## Tray and settings

| ID | Given / When | Then |
| --- | --- | --- |
| UX-TRY-01 | App starts | A tray icon appears within 2 s; a second launch shows a notification in the running instance instead of starting another. |
| UX-TRY-02 | Left-click / double-click tray icon | A click starts a region capture once the double-click interval passes; a double-click opens the screenshots folder instead and starts no capture. |
| UX-TRY-03 | Right-click tray icon | Menu: capture actions (with hotkeys), Open Image…, Open screenshots folder (the save folder, created if missing; a failure is a warning), Settings…, Check for updates (UX-TRY-11), About, Exit. |
| UX-TRY-06 | `Ctrl+,` or File → Settings… in an editor | Opens the same Settings dialog as the tray. |
| UX-TRY-04 | Settings saved, or open | While Settings is open, captures work per UX-G-10. Once saved, hotkeys re-register immediately and menu labels update; conflicts reported per UX-CAP-10. Every visible setting changes behavior. Run-at-login is a "VerdiClip" shortcut with the app icon in the user's Startup folder, so Settings → Apps → Startup and Task Manager list it by name and icon (it replaces the pre-0.5 Run-key value, which is removed). Run-at-login, Open with, and the Start menu entry, when on, are also rewritten at every launch so they follow a moved install and repair lost entries — except that a launch never takes over a Startup or Start menu shortcut, or the Open with registration, that starts a different install (another environment folder) which still exists (such as running a second checkout for testing), and neither does saving unrelated settings; turning the setting on points it at the running install. |
| UX-TRY-07 | Settings → General → Open with / default app | "Show VerdiClip in Open with" registers VerdiClip for PNG, JPEG, BMP, GIF, TIFF, and WebP under the current user (no admin prompt), so Explorer's "Open with" lists it by name and icon; opening a file there opens it in an editor of the running instance. Unchecking removes only VerdiClip's entries. "Make VerdiClip the default image app…" turns that on, opens VerdiClip's page under Settings > Default apps, and shows a guide: the clicks Windows needs (each file type, choose VerdiClip, Set default), one row per type that ticks itself off within a second of the user's choice, and a button to reopen Settings. Windows does not let apps set their own default or offer a set-all button for image types. |
| UX-TRY-08 | Settings → General → "Show VerdiClip in the Start menu" | On by default: a "VerdiClip" shortcut with the app icon in the user's Start menu Programs folder, so searching Start for VerdiClip starts it (or notifies the running instance per UX-TRY-01). Unchecking removes only that shortcut. |
| UX-TRY-09 | Settings → Hotkeys → Record | Each hotkey can be typed or recorded: Record captures the next combination pressed, kept when its key comes back up (modifiers preview while held). Every key Windows hotkeys accept records, including PrtSc (even when the Snipping Tool or another app owns it), numpad keys (as Num 0–9, Num Multiply, and so on, distinct from the top-row digits), punctuation, and the Menu key; Esc alone cancels and keeps the old value, and a key Windows can't register (volume and media keys, Fn) asks for another. While recording, every key on the PC goes to the field, not to Windows or other apps, so VerdiClip's own hotkeys, the Snipping Tool and Win-key shortcuts stay quiet (Ctrl+Alt+Del and Win+L can't be held back); the keyboard is released when recording ends, is cancelled, Settings closes, switches tab or loses focus, or after 20 s without a key. |
| UX-TRY-10 | Help menu in an editor (last on the menu bar, after Tools) | Documentation (`F1`) opens the project's documentation on GitHub in the browser; About VerdiClip opens the same About box as the tray, over that editor, with a link to the documentation. |
| UX-TRY-11 | Tray → Check for updates, or automatically (a minute after start, then every 6 hours) while Settings → General → "Check for updates automatically" is on (the default) | VerdiClip asks PyPI for the latest release. A check from the menu always answers: "up to date" with both versions, "Update available" (clicking the balloon installs it per UX-TRY-12), or why the check failed. Automatic checks say nothing unless they find a newer release, and offer each release once. Once a newer release is known, the menu entry reads "Install VerdiClip X…". |
| UX-TRY-12 | Installing an update: Install VerdiClip X… in the tray, the "Update available" balloon, or automatically when "Install updates automatically when no image is open" is on (the default; greyed out while checking is off) | An automatic install waits until no editor or capture is open (it goes ahead when the last editor closes, unless checking or installing was turned off meanwhile); a manual one closes editors first, each asking per UX-G-06, and Cancel stops the update. VerdiClip exits, a helper on the base Python installs the release the way VerdiClip was installed — a git checkout on `main` with no local changes to tracked files: `git pull --ff-only` then `uv sync`; `uv tool install`: `uv tool upgrade`; pip or uv pip: an upgrade in that environment; uvx: fetch the release into uv's cache, then start it (or the old version if that fails) — and starts VerdiClip again through the same console-less command. The restarted app says "Updated from A to B", or why it failed and where the log is, once. An install that can't update itself (a checkout on another branch or with local changes, git or uv missing, an install it can't identify) says why, once per release when automatic, and keeps running. A release whose automatic update failed isn't retried automatically; it is offered instead. |
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
- [ ] Settings → General → "Start VerdiClip when I sign in": Settings → Apps → Startup lists "VerdiClip" with its icon, and it starts after signing out and in. Searching Start for "VerdiClip" finds the Start menu entry and launches it.
- [ ] Updates, from a checkout one release behind on `main`: Tray → Check for updates offers the release; installing it quits, pulls, syncs, and restarts without a console window, then says "Updated from A to B". The Start menu and Startup shortcuts still start it.
- [ ] Settings → General → Theme: Light, Dark, and Match Windows each restyle open editors (menus, toolbars, tool icons) without restarting; after switching Light → Dark, reopen Settings and confirm checked boxes are clearly visible.
