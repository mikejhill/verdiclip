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
The baseline has a ten-minute step deadline, against observed healthy runs of
about one minute. Expiry fails validation; it does not count as a mutation score.
The separate 260-minute job budget leaves time for always-run diagnostics and
result upload after that failure. Runner termination or an outage can still
prevent upload; the independent publisher treats missing evidence as incomplete.
Postflight rejects candidate or source drift before saving or reporting evidence.

The mutation command runs in its own POSIX process group. Its supervisor stops
work after at most 240 minutes, or at the job's 250-minute boundary if setup used
more time, within the 260-minute job limit. It attempts bounded
INT/TERM/KILL cleanup of that owned group. Identity verification, result collection
and cache sealing require an atomic `mutation-quiescence.json` receipt proving
the group retired for this exact commit, run and attempt. A child failure or
cancellation remains a failure even when cleanup succeeds. External runner
termination can interrupt cleanup; absent proof blocks cache saving. These are
cleanup reserves, not a promise the entire candidate set fits one job.

The work allowance is based on [run 174](https://github.com/mikejhill/verdiclip/actions/runs/42568107824/attempts/2):
the cold attempt used the former 130-minute allowance and left 2,360 candidates
unchecked; a verified same-commit continuation completed the campaign in about
89 more minutes. The roughly 219-minute aggregate supports a bounded four-hour
allowance, with ten minutes of setup slack and ten minutes after the cleanup
boundary. It does not prove that every cold campaign will finish: runner
variability, candidate order and repeated preparation affect elapsed time.
This changes the operational allowance only, not the baseline or per-test
deadlines, full scope, 72% floor, cache protections or asynchronous policy.
Longer runs occupy the serialized runner queue longer and can delay failure
notification. A timeout still fails and retains qualified progress; there is no
automatic retry loop. Weekly/manual campaigns still start cold, so a complete
single-run result can establish fresh evidence without relying on old verdicts.

[Run 175](https://github.com/mikejhill/verdiclip/actions/runs/42599317000)
subsequently exhausted the 240-minute work allowance with 16,255 of 17,934
candidates checked and 1,679 unchecked. It continued making progress and retired
cleanly, but this establishes that the larger allowance is not reliably sufficient.
Do not treat its partial score as a pass or increase the limit again without
measured evidence of the remaining work.

The locked forkserver's opt-in file logging is enabled to retain actual child
lifetimes, timestamped candidate starts/completions, requested test counts and
CPU/wall limits. `mutation-timing.log` and its three numbered rotations are
uploaded with the existing result artifact. The configured path is outside
`mutants/`, so restored verdict caches cannot supply old timing logs. The pinned
logger rotates at 10 MiB with three backups. During explicit collect warmup,
the pytest support plugin binds the mutmut rotating handler to the forkserver
process. Forked workers inherit that filter without rebinding it. This retains
the parent's candidate start/completion, lifetime and signal-exit records while
suppressing duplicate worker messages that raced during rotation in
[run 177](https://github.com/mikejhill/verdiclip/actions/runs/42637339465).
Retention limits, abrupt termination and wall-clock changes can still lose or
distort timing evidence; these remain diagnostic logs, not a complete ledger. Child lifetime includes more than the pytest
session, but does not measure all generation, warmup or campaign overhead.
Missing records are unknown, never zero-duration or successful candidates.
Logging does not change test selection, scheduling, deadlines, verdicts, freshness
qualification or the publisher's acceptance rules. Use these measurements to
choose a targeted improvement before considering a larger campaign design.

The retained-cache investigation for run 41969126494 found 16 zero-candidate
metadata files and one genuinely changed metadata digest per attempt. The hash
checks correctly rejected the changed files. The second cached snapshot contained
one more verdict than its structured result, demonstrating why writer retirement
must precede snapshots. `scripts/mutation_cache_audit.py` can inspect retained
JSON and ASTs without executing cached code; use the cache-generating Python
version because AST schemas differ. In pinned mutmut 3.8.0, the forkserver result
path stores zero candidate durations, so those fields cannot justify runtime
budget increases. See [the investigation](https://github.com/mikejhill/verdiclip/issues/70#issuecomment-6655510202).

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
An enclosing single-use scope owns cleanup, final propagation and validity
checks on every exit, including an early return or partial timer setup. Inner
callback scopes require that live owner. They still deliberately defer arbitrary
control exceptions across Qt; the protocol does not remove that policy. It makes
final propagation mandatory instead of depending on a later caller statement.
Skip/xfail cannot hide a genuine failure. Timeout and late completion fail the
test. The watchdog requires a responsive GUI thread and cannot replace the outer
CI deadline or prove the cause of a previously stalled run.

Required PR CI also runs the complete portable baseline on Linux/offscreen,
with the same ordered, non-native selection used before mutation. It first runs
the eight actual Qt guard proofs. The baseline has a ten-minute step cap inside
a fifteen-minute job cap, verbose test names, live captured output and a
45-second faulthandler stack diagnostic. A deadline or callback error fails CI;
none of these diagnostics turns an incomplete run into a mutation score.

Pure timer/clock tests cover these boundaries without importing Qt. Eight actual
offscreen Qt tests exercise assertion, cancellation, pytest outcomes and deadline
failure, including GeneratorExit and an application-specific control type. The
earlier helper passed the original six proofs on Qt 6.11.2 and 6.12.0 in PR68;
that is historical evidence, not qualification of the mandatory-scope revision.
Neither those proofs nor offscreen results establish native desktop behavior or
resolve PR68's separate cold-editor performance failure.

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

Run 177 completed all 17,934 candidates fresh at 75.88%, with no historical
verdicts, in 233m45s of mutation work. Only 6m15s remained before the work limit.
The retained 51-minute tail contained 1,589 completions; survivors accounted for
87.1% of their aggregate child lifetime. This shows costly test work in that
tail, not a gross scheduler stall, but is not CPU utilization or a complete
campaign profile. The earlier incomplete run still establishes timeout risk.
Do not reduce test coverage, thresholds or freshness requirements based on these
diagnostics.
