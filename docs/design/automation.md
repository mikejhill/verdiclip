# Automation: AI tools and scripts

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
| `ctl list` | Every document: id, title, path, unsaved, image size, crop, annotation count, state, window, tab, current, active |
| `ctl get [--doc ID]` | The summary plus annotations (the clipboard codec's JSON) and selected ids |
| `ctl render [--doc ID] [-o FILE] [--region X,Y,W,H] [--max-side PX] [--grid PX] [--base]` | Writes a PNG; returns path, size, origin, scale, grid, state |

- `--doc` defaults to the active document: the shown tab of the most recently
  activated editor window. Ids are `DocumentRecord.identifier`, stable when a tab
  moves between windows.
- Coordinates are original-image pixels. Image coordinate = `origin` + preview
  pixel / `scale`. Grid labels are image coordinates; a grid too dense for the
  scale doubles its step until lines are at least 40 px apart.
- Without `-o`, previews go to `%LOCALAPPDATA%\VerdiClip\automation` and are
  deleted after 24 hours.
- Exit codes: 0 ok, 1 failed, 2 bad usage, 3 not running or turned off,
  4 conflict, 5 not found, 6 busy.

## Later phases

1. **Edit** — `apply` batches (add, update, delete, restack, crop, rotate, flip,
   resize) as one undo step labelled "AI: …", `--expect-state` conflict checks,
   `history`, `undo`/`redo` with `--steps`, `--to` and `--only-ai`, `select`, and the
   skill with `ctl install-skill --agent claude,codex,agents,copilot [--scope user|project] [--dir PATH]`
   (an agent is required; scope defaults to user).
2. **Extras** — `open`, `save`, `copy`, `set_image`.

An MCP server, if a client ever needs one, is a thin wrapper over the same pipe.
