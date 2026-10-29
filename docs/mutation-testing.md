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
The separate 150-minute job budget leaves time for always-run diagnostics and
result upload after that failure. Runner termination or an outage can still
prevent upload; the independent publisher treats missing evidence as incomplete.
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

## Readiness test diagnostics

Qt exposure waits are context managers and must actually be entered/exited. A
portable AST audit rejects discarded exposure/activation context factories.
Latency journeys retain the original 300 ms assertion and do not warm editors or
pin test order to hide cold initialization. Failure-only notes retain inclusive
phase wall/process/thread CPU times; nested timings overlap, and low CPU cannot
distinguish native blocking from runner scheduling. Font-cache state is explicitly
uncontrolled rather than inferred from a green rerun. Diagnostic failures preserve
the original exception.

The owned modal journey rejects only its fixture dialog on callback failure and
uses cancellable, dialog-owned single-shot timers. Deadline expiry revokes queued
actions and fails the test; a monotonic check also rejects delayed timer admission
and late success. All timers are stopped independently, and callback errors are
re-raised outside Qt after cleanup. A QTimer cannot interrupt a blocked GUI thread;
the outer CI deadline is still necessary. Mock clock/timer tests prove these
ownership rules, while actual Qt delivery needs hosted widget validation. These
changes do not establish the cause of the retained fatal abort or latency outlier.

The canonical mandatory-scope helper described above retains cancellation and
pytest outcomes until exact owned cleanup, then propagates them on every owner
exit. PR68 preserves that reviewed main implementation and its 41 portable cases
and eight actual Qt proofs. The earlier broad-catch version and its six proofs
remain historical evidence. This integration does not restore the old boundary,
dismiss scanning findings or disable a query.

The cold-profile job retains the same first-capture 300 ms assertion in its own
process on Qt 6.11.2 and 6.12.0. StyleBar creates its native font picker only when
FONT fields are needed, so opening a Select editor no longer needs that work.
This defers the cost rather than eliminating it. Another fresh test process
records first-FONT activation and the real native picker constructor using
wall/process/thread clocks after that process constructs its editor; editor
startup may already load fonts, so this is not a pristine font-cache measurement.
A queued timer reports its first delivery delay;
this is not a bound on every possible GUI stall. OS font cache state is unknown.
The first-FONT measurement has no invented latency floor and does not replace
the capture budget. Its JSON is retained as a `first_font_timing` JUnit suite
property and printed by the job after test success. Separate actual Qt widget
tests check current-style propagation, native picker edits and height reservation
on both supported profiles. Offscreen proof remains distinct from native desktop
focus/input qualification.

The constructor diagnostic runs only after that unchanged cold case. Four
separate processes measure stock `QSpinBox`, `QLineEdit`, `QTextLayout` and style
hints on the same pinned Qt/QPA configuration. Each records application startup
separately and reads QPA/style/font-directory inventory only after measurement.
Every child has a 30-second deadline inside a three-minute outer step. Timeout,
nonzero exit or invalid evidence fails the diagnostic and retains an incomplete
JSON receipt; a slow but complete measurement has no invented speed threshold.

This comparison follows Qt's constructor path: the abstract spinbox creates a
line edit, whose private initialization creates a text control and configures
font/style data ([spinbox source][qt-spin-init], [line-edit source][qt-line-init]).
The text control performs layout even for its initial empty string
([layout source][qt-line-layout]). Python constructor wrappers do not intercept
these internal C++ allocations. Independent primitive timings cannot partition
the actual spinbox constructor or prove font enumeration caused a delay. A fresh
process also does not imply a cold OS font cache. No alternate backend, warmed
path or reduced acceptance threshold replaces the failing product test.

The later primitive probes may inherit OS file-cache warmth from the preceding
capture and font-control tests. An independent standard Windows job therefore
runs only the stock spinbox first, once per supported Qt profile, without any
earlier editor, font or modal test on that VM. Its duration is informational;
complete validated measurement is required, not a new speed threshold. This job
does not warm or replace the product's first-capture job or its 300 ms assertion.

Both jobs collect a fixed allowlist of source/run identity, runner image version,
Windows build, actual Qt/QPA/style and font-directory inventory after measurement.
The product timer is sampled at the original exposure endpoint before metadata
is collected. Missing or differing profile facts make comparison incomparable.
Even equal facts and independent VMs do not prove equally cold OS caches or an
internal cause. Pytest hooks, prior product construction and extension-method
patches remain distinct context factors for later matched subsets if needed.

The stock-first matrix also records an intentional `windows` QPA contrast on
separate standard GitHub Windows VMs. It constructs one stock widget without
Show, activation, input, global hotkeys or explicit clipboard-content calls.
Qt's Windows integration automatically initializes native context, screen/input
integration and a hidden clipboard listener. The child remains bounded to 30
seconds; successful subprocess completion observes process exit. Cleanup is
process-bound rather than an independent native resource inventory. No local
desktop, new driver, trust store or system setting is involved.

This changes only the stock diagnostic matrix. The offscreen capture jobs and
their 300 ms criterion remain unchanged. The strict environment comparator must
reject QPA/style differences; native/offscreen values are an intentional backend
contrast, not identical-environment parity or a replacement passing gate. Qt's
[Windows integration][qt-native-fonts] selects a backend-specific font database,
while [offscreen on Windows][qt-offscreen-fonts] uses the generic FreeType path.
The actual binary font implementation and OS cache state remain unproven. A
hidden stock constructor timing cannot qualify visible editor readiness,
foreground preservation, broker behavior, capture delivery or native UX.

[qt-native-fonts]: https://github.com/qt/qtbase/blob/v6.12.0/src/plugins/platforms/windows/qwindowsintegration.cpp#L444
[qt-offscreen-fonts]: https://github.com/qt/qtbase/blob/v6.12.0/src/plugins/platforms/offscreen/qoffscreenintegration.cpp#L65

[qt-spin-init]: https://github.com/qt/qtbase/blob/v6.11.2/src/widgets/widgets/qabstractspinbox.cpp#L1512
[qt-line-init]: https://github.com/qt/qtbase/blob/v6.11.2/src/widgets/widgets/qlineedit_p.cpp#L163
[qt-line-layout]: https://github.com/qt/qtbase/blob/v6.11.2/src/widgets/widgets/qwidgetlinecontrol.cpp#L306
