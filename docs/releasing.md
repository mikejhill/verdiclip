# Releasing VerdiClip

Releases are automatic. Versions follow [SemVer](https://semver.org/) and are computed from [Conventional Commits](https://www.conventionalcommits.org/) on `main` by [release-please](https://github.com/googleapis/release-please).

## How a release happens

1. Pull requests are squash-merged into `main`; the PR title (checked by CI) becomes the commit message.
2. On every push to `main`, the **Release** workflow opens or updates a release pull request titled `chore: release X.Y.Z`. It bumps the version in `pyproject.toml` and `uv.lock`, updates `CHANGELOG.md`, and dispatches CI on the release branch so the pull request gets the usual checks. (A `RELEASE_PLEASE_TOKEN` secret is optional; without it the default token is used.)
3. Merging the release pull request tags `vX.Y.Z` and creates the GitHub release. The same workflow then:
   - runs the full CI suite against the release commit,
   - builds the sdist and wheel once and checks the version matches the tag,
   - creates build-provenance attestations,
   - publishes to PyPI with trusted publishing (no API tokens; PEP 740 attestations included),
   - attaches the distributions to the GitHub release.

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

Run the manual checklist at the end of [ux-contract.md](design/ux-contract.md) on a Windows machine: real `PrtSc` hotkeys, multi-monitor and mixed-DPI capture, clipboard pasting into other apps, printing, and theme switching.
