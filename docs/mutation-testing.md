# Asynchronous mutation testing

[Mutation runs](https://github.com/mikejhill/verdiclip/actions/workflows/mutation.yml)
run after pushes to `main`, Mondays at 05:23 UTC, and manual dispatch on the
default branch. Other manual refs are rejected. Mutation is independent of PR,
merge and release checks. Existing coverage, CI and CodeQL gates remain intact;
no repository protection or security settings are changed.

The configured whole-package floor remains **72%**. Complete comparable scores
**at or above 72% pass**, including equality. Surviving mutants remain available
for meaningful test improvement; their presence alone does not create an issue.
Timeouts and segfaults retain their distinct raw verdicts; counting them as detected
does not establish that every such mutant represents correct behavior.

## Durable alerts and triage

The separate [alert publisher](https://github.com/mikejhill/verdiclip/actions/workflows/mutation-alerts.yml)
maintains at most one findings issue and one execution issue, assigned to
`mikejhill`. It reopens existing trackers instead of creating duplicates.

- **Findings:** a complete comparable score below the configured floor. Review
  survivor diffs, add assertions for meaningful behavior and document equivalent
  mutants. Never lower the floor just to clear an alert.
- **Execution:** missing, malformed, skipped, cancelled or incomplete evidence,
  setup/baseline/identity failures, or an unexpected workflow failure. Repair
  the execution; a timeout is not a passing score. A later successful execution
  does not prove the original failure's root cause was fixed.

Triage within one working day and review open trackers weekly. Keep investigation
notes in comments because the publisher manages issue bodies. Each tracker links
the run, source revision, scope, floor, score and verdict counts when established.
The run summary and attempt-specific `mutation-results-N` artifact retain raw
verdicts, baseline output, mutation logs and identity receipts. If cancellation
prevents upload, the publisher still reports execution failure from run metadata.
Publisher API/permission failures are visible as failed alert workflow runs and
must also be investigated; issue delivery cannot be guaranteed during an outage.

Only newer complete **fresh** evidence for the same scope, policy and floor can
close findings. A changed floor cannot silently clear an older policy tracker.
Incomplete runs preserve findings. Delayed older events cannot overwrite newer
evidence, including rerun attempts. Duplicate or malformed managed trackers fail
closed for manual reconciliation rather than being overwritten.

## Cache and evidence limits

Push reruns can resume verdicts only for the **same complete commit, OS and
architecture**, with the same configuration/test fingerprint. Candidate identity
alone is insufficient: an unchanged function can call a changed helper, changing
a mutant's result. Cross-commit restoration is prohibited even when candidate
bodies match. The restored cache must also contain the exact qualified context;
missing, legacy or mismatched caches are quarantined before reconciliation.

Generated candidate bodies and original sources must still match verified
provenance from the locked mutmut 3.8.0 generator. Context is sealed only after
identity verification, including for incomplete progress saved for same-commit
resumption. Legacy ordinal-only verdicts are not reused. Weekly/manual runs start
cold. Every run
qualifies the locked generator and requires a passing baseline before mutation.
Postflight rejects candidate or source drift before saving or reporting evidence.

Schema 2 result evidence must carry the qualified current commit/OS/architecture
context and successful context qualification. The publisher currently admits the
reviewed Linux/X64 profile only. Legacy schema 1, missing context or mismatched
context is **incomplete execution evidence**, regardless of an informational
score
or `fresh: false` label; it cannot create current score findings, clear findings
or
recover execution health. Prior artifacts remain informational. Existing findings
for an older policy remain open for explicit reconciliation rather than being
silently cleared by an incomparable new policy.

Resumed same-context verdicts remain explicitly historical. A cached pass cannot
automatically resolve findings; use a fresh weekly or manual full run. Partial
caches can be retained only after context and identity verification
so a later push can resume useful work. Healthy runs are not cancelled by newer
pushes. GitHub's concurrency queue can replace pending jobs; default-branch runs
qualify their actual recorded revision rather than promising every pushed commit
will execute.

## Permissions and validation

The editor-modal journey uses a test-owned loop guard. Callback failure rejects
only its fixture dialog, revokes queued actions and disarms each owned timer.
The original exception is re-raised outside the Qt callback boundary, including
interpreter cancellation; secondary errors remain in the explicit cause group.
Skip/xfail cannot hide a genuine failure. Timeout and late completion fail the
test. The watchdog requires a responsive GUI thread and cannot replace the outer
CI deadline or prove the cause of a previously stalled run.

Required PR CI also runs the complete portable baseline on Linux/offscreen,
with the same ordered, non-native selection used before mutation. It first runs
the six actual Qt guard proofs. The baseline has a ten-minute step cap inside
a fifteen-minute job cap, verbose test names, live captured output and a
45-second faulthandler stack diagnostic. A deadline or callback error fails CI;
none of these diagnostics turns an incomplete run into a mutation score.

Pure timer/clock tests cover these boundaries without importing Qt. Six actual
offscreen Qt tests exercise assertion, cancellation, pytest outcomes and deadline
failure. Identical helper/proof sources previously passed all six tests on Qt
6.11.2 and 6.12.0 in PR68; that evidence does not establish native desktop behavior
or resolve PR68's separate cold-editor performance failure.

The executor has `contents: read`. The serialized publisher has only
`contents: read`, `actions: read` and `issues: write`. It checks out trusted
default-branch publisher code with no persisted checkout credential. It consumes
one bounded JSON data file from the same repository's completed default-branch
run; artifact files are never executed. No PR code, repository secrets, OIDC or
downstream privileged workflow is used. Platform job tokens still exist.

`workflow_run` is retained so a separate publisher can observe cancellation and
hard timeout when a same-run job cannot execute. Its categorical zizmor warning
has one documented inline exception, independently reviewed against
[zizmor's case-by-case guidance](https://docs.zizmor.sh/audits/#dangerous-triggers).
Admission rejects foreign repositories, PR events and non-default branches
before artifact download. Only trusted default-branch publisher code runs.
Artifacts download into `runner.temp`, outside the executable checkout, as
[GitHub recommends](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run).
The reader requires the fixed attempt's bounded UTF-8 JSON file, rejects symlinks
and duplicate keys, validates run identity/accounting and never uses artifact
strings as shell commands, paths or executable code. The exception does not
disable other security audits or repository protections. Changes to any of these
boundaries require renewed source review.

Normal PR CI tests the publisher with mock GitHub APIs: threshold boundaries,
create/update/reopen/recovery, pagination, incomplete/malformed evidence,
historical verdicts, incomparable policies and stale ordering. Structured
evidence and candidate identity tests are portable. The actual mutation executor
requires Linux. The new trigger and publisher take effect only after this change
is on the default branch; mock tests are not a live alert-delivery qualification.
