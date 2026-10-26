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

Tab ownership/transfer/topology, fake hotkey routing and accessibility contracts
have a reproducible primary CI target: `uv run poe test-tabs-ci`. The two explicit
Qt runtime jobs require separate scoped line and branch coverage of at least 90%
and upload source/runtime/seed provenance with JUnit, logs and XML coverage. Native
laptop checks are supplemental, not required infrastructure. See the
[tab CI contract](docs/design/editor-tabs-ci-contract.md) for simulation boundaries
and replay instructions. The whole-app and mutation gates below still apply.

The real desktop smoke test runs automatically on Windows with an unlocked
session. A positively identified locked or disconnected session skips only
that test with the prerequisite reason; unknown session state and capture
errors still fail. Native pixel conversion, buffer detachment, handle reuse,
and native error cleanup remain covered by deterministic tests on every run.

Before merging capture changes or releasing, unlock/reconnect the Windows
desktop and require the real smoke test to pass:

```bash
uv run pytest tests/capture/test_grabber.py::TestMssScreenSource::test_freeze --require-interactive-capture -v
```

This mode fails if the desktop prerequisite is absent. A normal green suite
with the locked-desktop skip is not evidence that real capture passed. Never
turn a `CaptureError` or `BitBlt` failure on an available desktop into a skip.

Before releasing capture lifecycle changes, also run the native popup/modal
matrix. It uses real desktop pixels and the actual tray/controller widgets,
with test-only hotkey messages and temporary preferences:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
uv run pytest tests/capture/test_capture_native.py --require-interactive-capture
```

See [capture input ownership](docs/design/capture-input.md) for the failure
mechanism, lifecycle invariant, and limits of automated validation.

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
