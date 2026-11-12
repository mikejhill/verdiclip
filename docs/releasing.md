# Releasing VerdiClip

Releases are automatic. Versions follow [SemVer](https://semver.org/) and are computed from [Conventional Commits](https://www.conventionalcommits.org/) on `main` by [release-please](https://github.com/googleapis/release-please).

## How a release happens

1. Pull requests are squash-merged into `main`; the PR title (checked by CI) becomes the commit message.
2. On every push to `main`, the **Release** workflow opens or updates a release pull request titled `chore: release X.Y.Z`. It bumps the version in `pyproject.toml` and `uv.lock`, updates `CHANGELOG.md`, and approves the pull request's CI runs (GitHub holds runs on bot-opened pull requests for approval), so the release pull request gets the usual required checks. (A `RELEASE_PLEASE_TOKEN` secret is optional; without it the default token is used.)
3. Merging the release pull request tags `vX.Y.Z` and creates the GitHub release. The same workflow then:
   - runs the full CI suite against the release commit,
   - builds the sdist and wheel once and checks the version matches the tag,
   - creates build-provenance attestations,
   - publishes to PyPI with trusted publishing (no API tokens; PEP 740 attestations included),
   - attaches the distributions to the GitHub release.

Running copies pick the release up from PyPI by themselves: VerdiClip checks a minute after it starts and every 6 hours, and installs a newer release when no image is open, whether it runs from `uv tool`, pip, uvx, or a checkout on `main` (UX-TRY-11, UX-TRY-12). A checkout pulls `main`, so it also gets anything merged after the release.

While the version is below 1.0, breaking changes bump the minor version.

| Commit | Example | Next version from 0.4.2 |
| --- | --- | --- |
| `fix:` / `perf:` | `fix: keep crop inside the image` | 0.4.3 |
| `feat:` | `feat: add blur obfuscation` | 0.5.0 |
| `feat!:` / `BREAKING CHANGE:` | `feat!: new settings schema` | 0.5.0 (1.0+ → major) |
| others | `docs:`, `ci:`, `chore:` … | no release |

To force a specific version, add a commit with a `Release-As: 1.0.0` footer.

## One-time setup

These need an account owner; the workflows cannot do them.

1. **PyPI trusted publisher.** On [pypi.org](https://pypi.org/manage/account/publishing/), add a *pending publisher*: project `verdiclip`, owner `mikejhill`, repository `verdiclip`, workflow `release.yml`, environment `pypi`.
2. **TestPyPI (optional dry run).** Do the same on [test.pypi.org](https://test.pypi.org/manage/account/publishing/) with environment `testpypi`. Then run **Actions → Release → Run workflow → testpypi** to publish the current `main` there.
3. **Let Actions open pull requests.** Settings → Actions → General → Workflow permissions → enable *Allow GitHub Actions to create and approve pull requests*.
4. **Protect `main`.** Require the **CI passed** check (it aggregates every CI job) and allow squash merging only, using the PR title as the commit message.
5. **Optional:** add a required reviewer to the `pypi` environment so publishing waits for an approval.

## Before merging a release pull request

Automated repository checks are the normal release gate. Physical-machine smoke
checks are ad hoc, required only for critical behavior that the relevant automated
evidence cannot reliably verify. A release does not routinely require a person's
laptop or completion of every item in the physical checklist.

1. Require the final release head's CI and security checks to pass. Inspect all PR
   review threads and code-scanning findings as described in
   [CONTRIBUTING.md](../CONTRIBUTING.md). Preserve the exact-main verification gate
   before publication; do not weaken coverage, latency or security requirements.
   Publication requires the exact main CI and CodeQL results (both include the
   real desktop smoke and the native popup/modal/tab regressions) and no open
   code-scanning alerts. A missing, skipped or failed run cannot be replaced by an
   earlier feature-branch result; re-running a flaky main job is allowed, and the
   gate judges its latest attempt.
2. Review the changed UX contracts and their automated evidence, including
   interrupted/repeated flows, focused editors and the applicable Qt profiles.
   Review light/dark snapshots for visible changes. Record the final commit, run
   links, relevant results and any verification limits in the release PR.
3. Retain the real desktop capture smoke required by CONTRIBUTING. A passing
   `TestMssScreenSource::test_freeze` result from the exact candidate's hosted
   Windows CI is valid evidence: this test captures the actual runner desktop.
   Check the test result itself, not just the aggregate check. A locked-desktop
   skip is not a pass; if needed, run the documented
   `--require-interactive-capture` command on an available Windows desktop.
   This automated native check does not require a physical-machine test by the
   user, and does not prove physical key delivery or mixed-monitor behavior.
4. For capture lifecycle changes, also retain the native popup/modal matrix and
   any change-specific native/baseline qualification required by
   [capture input ownership](design/capture-input.md). Simulated tab/hotkey tests
   do not replace these native contracts.
5. Assess whether a critical gap requires a physical smoke check using the
   [checklist](design/ux-contract.md#manual-checklist). Examples include changed
   physical hotkey registration, DPI/monitor integration not represented by the
   automated matrix, an unresolved real-device regression, or a specifically
   requested user acceptance check. Run the relevant cases, not the entire list
   by default; state the risk and expected result before requesting a session.
   Existing explicit user acceptance requirements and release holds still apply.

When the affected behavior is covered reliably by CI and no critical physical
integration gap is identified, record that assessment and proceed without a
manual session. Never label simulated evidence as physical/native validation or
claim an unperformed check passed. Expand reproducible automated coverage when
practical; keep remaining limits explicit.
