# Automation: the `verdiclip ctl` command

Local tools (Claude Code, Codex, scripts) read and, in later phases, edit the
images open in VerdiClip's editor. The running app owns the documents, their
undo history and the renderer, so tools ask the app instead of touching files.

```
AI tool ──► verdiclip ctl ──► per-user control pipe ──► ControlServer (GUI thread)
   ▲                                                         │
   └──────────── PNG path + JSON ◄── Renderer.flatten ◄── open document
```

## Transport

| Aspect | Choice |
| --- | --- |
| Pipe | `QLocalServer` named `VerdiClip-<user>-control` (`\\.\pipe\…` on Windows) |
| Access | `UserAccessOption`: only the same Windows user can connect |
| Framing | One UTF-8 JSON object per line; one response per request, in order |
| Request | `{"v": 1, "id": 1, "method": "render", "params": {…}}` |
| Response | `{"id": 1, "ok": true, "result": {…}}` or `{"id": 1, "ok": false, "error": {"code": "…", "message": "…"}}` |
| Switch | Settings → General; off answers every request with `disabled` |

The single-instance pipe (`VerdiClip-<user>`, `shell/instance.py`) stays separate:
it is one-way (a second launch forwards file paths and disconnects), and older
builds still use it.

The client (`automation/client.py`) never imports Qt: it opens the Windows named
pipe as a file, or the Unix socket Qt uses elsewhere. The `verdiclip` entry point
still loads PySide6 when it starts, but `ctl` creates no Qt application and
configures no logging.

## Commands (phase 1)

| Command | Result |
| --- | --- |
| `ctl status` | App version, protocol version, document count |
| `ctl list` | Every document: id, title, path, unsaved, image size, crop, annotation count, state, window, tab, current, editing, active |
| `ctl get [--doc ID]` | The summary plus annotations (the clipboard codec's JSON) and selected ids |
| `ctl render [--doc ID] [-o FILE] [--region X,Y,W,H] [--max-side PX] [--grid PX] [--base]` | Writes a PNG; returns path, size, origin, scale, grid, state |

- `--doc` defaults to the active document: the shown tab of the most recently
  activated editor window. Ids are `DocumentRecord.identifier`, stable when a tab
  moves between windows.
- Coordinates are original-image pixels. Image coordinate = `origin` + preview
  pixel / `scale`. Grid labels are image coordinates; a grid too dense for the
  scale doubles its step until lines are at least 40 px apart.
- While the user types in an inline editor, `get` and `render` for that
  document answer `busy`: the typed text isn't in the document until it is
  committed. `list` marks such documents `"editing": true`.
- Without `-o`, previews go to `%LOCALAPPDATA%\VerdiClip\automation` and are
  deleted after 24 hours.
- Exit codes: 0 ok, 1 failed, 2 bad usage, 3 not running or turned off,
  4 conflict, 5 not found, 6 busy.

## Commands (phase 2a: editing)

| Command | Result |
| --- | --- |
| `ctl apply [--doc ID] --ops FILE\|- [--label TEXT] [--select] [--expect-state N]` | Runs the operations as one undo step "CLI: …"; returns state, label, added ids |
| `ctl history [--doc ID]` | `undo` steps oldest first, `redo` steps next first: label, state, `cli` |
| `ctl undo\|redo [--doc ID] [--steps N \| --to STATE] [--only-cli] [--expect-state N]` | Moves through history; validates the whole move first |
| `ctl select [--doc ID] [IDS...]` | Sets the selection; no ids clears it |

Operations (`--ops` holds the array, or `{"ops": [...]}`), applied in order to a
copy of the document; the real one changes only when all succeed:

| Operation | Fields |
| --- | --- |
| `add` | `annotation`: `kind` plus geometry as `get` prints it; `id`, `style` fields and a counter's `label`/`radius` are optional |
| `update` | `id`, `set`: fields to change; `style` fields merge one by one; `id` and `kind` can't change |
| `delete` | `ids` |
| `restack` | `ids`, `to`: `top` or `bottom` (relative order kept) |
| `crop` | `rect`: `[x, y, w, h]` in image pixels |
| `rotate` | `direction`: `right` or `left` |
| `flip` | `axis`: `horizontal` or `vertical` |
| `resize` | `width`, `height`: the visible area's new size |

- Later operations see earlier ones: ids added earlier can be updated, and
  coordinates after a `rotate` or `resize` are in the new image.
- Edits answer `busy` while the user types or drags in the image, and
  `conflict` when `--expect-state` no longer matches.
- `history` marks each step `cli: true` when `verdiclip ctl` made it; `--only-cli`
  refuses to undo or redo past a step made in the editor.

## Skill (phase 2b)

`src/verdiclip/automation/skill/SKILL.md` ships in the package and teaches an
agent the workflow: list, render and look, get the state, apply with
`--expect-state`, render again; plus history and the rules (never save or close,
never undo the user's steps unasked).

`ctl install-skill --agent LIST [--scope user|project] [--dir PATH]` copies it
to `<skills folder>/verdiclip/SKILL.md`, replacing an older copy, without
contacting the app. An agent is always required; scope defaults to user.

| Agent | User scope | Project scope |
| --- | --- | --- |
| `claude` | `~/.claude/skills` | `.claude/skills` |
| `codex` | `~/.codex/skills` | `.codex/skills` |
| `agents` | `~/.agents/skills` | `.agents/skills` |
| `copilot` | `~/.copilot/skills` | `.github/skills` |

`--dir` replaces the agent's folder and takes one agent.

## Later phases

1. **Extras** — `open`, `save`, `copy`, `set_image`.

An MCP server, if a client ever needs one, is a thin wrapper over the same pipe.
