---
name: verdiclip
description: Read, view and annotate screenshots open in the user's VerdiClip editor (boxes, arrows, text, numbered markers, highlights, blur, crop, rotate), including unsaved edits, through the `verdiclip ctl` command. Use when the user asks about or wants changes to an image open in VerdiClip.
---

# VerdiClip

VerdiClip is a screenshot editor running on the user's PC. `verdiclip ctl` talks
to the running app: it reads the images open in its editor windows, unsaved
edits included, and changes them. Each change appears on the user's screen at
once and is one step in the editor's undo history, named by `--label`.

Every command prints JSON. Exit codes: 0 ok, 1 failed, 2 bad usage, 3 VerdiClip
not running or automation turned off (Settings → General), 4 conflict, 5 not
found, 6 busy (the user is typing or dragging; wait a moment and retry).

## Workflow

1. **Find the image.** `verdiclip ctl list` shows every open image with its
   `id`, window, tab and `active` (the tab the user is looking at). Commands
   without `--doc ID` use the active image. Use an explicit `--doc` when more
   than one image is open and the user didn't mean the one in front.
2. **Look at it.** `verdiclip ctl render --grid 100` writes a PNG and prints its
   `path`. Open that file with your image viewer (Claude Code: Read the path;
   Codex: view_image). The grid is labelled in image pixels, which is the
   coordinate system every command uses. For detail, render a region:
   `--region X,Y,W,H` (the reply's `origin` and `scale` map preview pixels back:
   image = origin + preview / scale).
3. **Read the model.** `verdiclip ctl get` returns the annotations (with ids),
   crop, selection and `state`.
4. **Edit.** Write the operations to a file and run
   `verdiclip ctl apply --ops ops.json --expect-state STATE --label "what you did"`,
   using the `state` from step 3. Exit code 4 means the user changed the image
   meanwhile: go back to step 3.
5. **Check.** Render again and look before telling the user it's done.

## Operations

`--ops` takes a JSON array (or `{"ops": [...]}`; `-` reads stdin). They run in
order as one undo step; if any fails, nothing changes and the error names the
operation number. Later operations see earlier ones.

```json
[
  {"op": "add", "annotation": {"kind": "rectangle", "rect": [40, 60, 200, 80]}},
  {"op": "add", "annotation": {"kind": "arrow", "start": [300, 40], "end": [245, 95],
                               "style": {"stroke": "#e53935", "width": 4}}},
  {"op": "add", "annotation": {"kind": "counter", "center": [30, 50]}},
  {"op": "update", "id": "ID", "set": {"text": "Fixed", "style": {"stroke": "#43a047"}}},
  {"op": "delete", "ids": ["ID"]},
  {"op": "restack", "ids": ["ID"], "to": "top"},
  {"op": "crop", "rect": [0, 0, 800, 600]},
  {"op": "rotate", "direction": "right"},
  {"op": "flip", "axis": "horizontal"},
  {"op": "resize", "width": 1200, "height": 900}
]
```

Annotation kinds and their geometry (image pixels, `[x, y, width, height]` for
rects, `[x, y]` for points):

| kind | fields |
| --- | --- |
| `rectangle`, `ellipse` | `rect`, optional `text` (label inside) |
| `line`, `arrow` | `start`, `end` |
| `text` | `rect`, `text`, optional `auto_fit` (default true: the box is resized to fit the text from the rect's top-left; false keeps the rect and wraps) |
| `counter` | `center`; `label` defaults to the next number, `radius` to the tool size |
| `highlight` | `rect` (translucent marker) |
| `obfuscate` | `rect` (pixelates what is beneath: use it to hide secrets) |
| `freehand` | `points`: `[[x, y], ...]` |

- `id` is optional on `add`; the reply's `added` lists the new ids.
- `style` is optional; omitted fields use the user's current tool style. Fields:
  `stroke`, `fill`, `outline` (`#rrggbb` or `#rrggbbaa`; `#00000000` is
  transparent), `width`, `font_family`, `font_size`, `bold`, `italic`, `shadow`,
  `h_align` (`left`/`center`/`right`), `v_align` (`top`/`middle`/`bottom`).
- `update` merges `set` into the annotation as `get` prints it; style fields
  merge one by one. `id` and `kind` can't change.
- After `rotate` or `resize`, later coordinates are in the new image.
- `--select` selects what was added, so the user sees it highlighted.

## History

- `verdiclip ctl history` lists `undo` steps (oldest first) and `redo` steps
  (next first), each with `label`, `state` and `ctl` (true for steps made through `verdiclip ctl`).
- `verdiclip ctl undo --only-ctl` undoes your last step and refuses (exit 4)
  rather than undo a step made in the editor. `--steps N` or `--to STATE` move
  further; `redo` takes the same options.
- Never undo the user's own steps unless they ask.

## Rules

- Don't save, copy or close images: the user decides what happens to them.
- Keep coordinates inside the image (`list` gives `image` size and `crop`).
- Prefer one `apply` per user request so one Undo reverts it.
- If a command answers busy (6), wait a second and retry; don't loop forever.
