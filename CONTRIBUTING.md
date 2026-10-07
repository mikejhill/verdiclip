# Contributing to VerdiClip

Thanks for helping. Read [docs/design/philosophy.md](docs/design/philosophy.md) first — it explains what VerdiClip is for and the principles every change must respect.

## Setup

```bash
uv sync
uv run poe check   # Must pass before you open a pull request
```

## Making a change

1. **UX first.** If the change is visible to users, add or update the row in [docs/design/ux-contract.md](docs/design/ux-contract.md) and a journey test in `tests/ux/` that cites its ID. Journeys drive real widgets and assert only on what a user can observe.
2. **Model changes go through commands.** Never mutate a `Document` outside a `Command` executed by `History`.
3. **Look at it.** Run `uv run poe ux-snapshots` and `uv run poe ux-gallery` and review the images in both themes.
4. **Keep the gate green.** `uv run poe check` runs formatting, lint, strict type checking (ty, every rule at error), and tests with ≥ 90% branch coverage. Don't weaken the configuration to get a pass.

## Commit messages

Commits and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/). Releases are versioned automatically from them:

| Prefix | Meaning | Version bump |
| --- | --- | --- |
| `feat:` | A user-visible feature | minor |
| `fix:` | A bug fix | patch |
| `perf:` | A performance improvement | patch |
| `feat!:` or a `BREAKING CHANGE:` footer | An incompatible change | major (minor while < 1.0) |
| `docs:`, `test:`, `refactor:`, `build:`, `ci:`, `chore:`, `style:` | No release on their own | — |

Pull requests are squash-merged, so the pull request title becomes the commit message; CI checks it.

## Releases

See [docs/releasing.md](docs/releasing.md).
