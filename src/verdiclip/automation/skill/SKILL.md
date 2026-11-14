---
name: verdiclip
description: Take screenshots (region, window, monitor, scrolling page) and open, view, annotate, edit, save, export, copy or close images in the user's VerdiClip editor (boxes, arrows, text, numbered markers, highlights, blur, crop, rotate, replace pixels), unsaved edits included, through the `verdiclip ctl` command. Use when the user asks for a screenshot or about an image in VerdiClip.
---

# VerdiClip

VerdiClip is a screenshot editor running on the user's Windows PC.
`verdiclip ctl` drives it: captures and opened files land in editor tabs on the
user's screen, and every edit is one undo step named "CLI: …".

**Read the help first; it is the reference.** `verdiclip ctl --help` gives the
workflow, commands and exit codes; `verdiclip ctl COMMAND --help` gives each
command's options (`capture --help` for capture types and targets,
`apply --help` for the edit operations and annotation fields).

## How to work

- Capture with `verdiclip ctl capture MODE …` or open with
  `verdiclip ctl open PATH`; keep the printed `id` and pass `--doc ID` after.
  `verdiclip ctl targets` lists monitors and windows for captures.
- Look before and after editing: `verdiclip ctl render --grid 100` prints a PNG
  path. View it (Claude Code: Read the path; Codex: view_image) and use the
  grid's image-pixel coordinates.
- Read `state` from `verdiclip ctl get` and pass it as `--expect-state` to
  `apply`, `save`, `export`, `copy` and `close`. Exit 4 means the user changed
  the image: read it again.
- Prefer one `apply` per request so one Undo reverts it. Undo only your own
  steps (`undo --only-cli`) unless the user asks.

## Rules

- Save, export, copy or close only when the user asks. Never overwrite a file
  the user didn't name. Never `close --discard` without the user's say-so.
- An interactive capture (`capture region` with no `--region`, or
  `capture scrolling` with no target) waits for the user: tell them what to
  click first. Exit 7 means they cancelled.
- Screenshots can hold private information. Don't send them anywhere the user
  didn't ask for.
- Exit 3: VerdiClip isn't running or automation is off (Settings → General).
  Exit 6: busy; wait a second and retry a few times, not forever.
