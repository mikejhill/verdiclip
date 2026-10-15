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

## Test quality

Coverage shows code ran, not that a test checked it. These keep the tests honest:

| Check | Where | What it catches |
| --- | --- | --- |
| Branch coverage ≥ 90% | `poe test`, CI | Code no test runs |
| Diff coverage ≥ 95% | CI, pull requests | New or changed lines without tests |
| Random test order ([pytest-randomly](https://github.com/pytest-dev/pytest-randomly)) | Every run | Tests that pass only after another test; rerun a failure with the printed `-p randomly --randomly-seed=N` |
| Property tests ([Hypothesis](https://hypothesis.readthedocs.io/)) | `tests/test_properties.py` | Edge cases hand-picked examples miss |
| Mutation testing ([mutmut](https://github.com/boxed/mutmut)) | CI: report on the modules a pull request changes; weekly whole-package run must score ≥ `MUTATION_FLOOR` | Code that runs under test but whose behavior no assertion checks |

Run mutation testing locally on Linux or WSL (mutmut forks, so not on native Windows):

```bash
uv run --group mutation poe mutate                     # whole package
uv run --group mutation mutmut run "verdiclip.geometry.*"   # one module
uv run --group mutation mutmut show <mutant-name>      # what a survivor changed
```

mutmut keeps its verdicts in `mutants/`, so running it again without a pattern retests only the functions you changed. A pattern retests every mutant it matches. Delete `mutants/` for a fresh score, for example after changing only tests. CI does the same: pull requests and pushes to `main` reuse cached verdicts, and the weekly run starts fresh.

A surviving mutant is a change no test noticed: add an assertion that fails for it, or mark the line `# pragma: no mutate` if the change is truly equivalent.

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
