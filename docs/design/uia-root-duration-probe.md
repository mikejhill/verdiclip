# One cold UIA root-duration probe

Diagnostic branch only; not a scrolling fix or a new latency gate. Run the
unchanged CI workflow once at a verified source SHA. The existing cold WinForms
profile is the target; retain every existing profile's outcome, without reruns.

The helper reads Stopwatch timestamps around the existing FromHandle call and
emits only the integer difference in microseconds after a successful return.
The tiny timing wrapper overhead is included. Both reads and emission remain
inside the existing 3,000 ms operation deadline. Startup, fixture, wrapper,
cleanup and ownership policies remain unchanged. No UIA warmup or new provider
query is introduced. Optional timing failures must not alter operation outcomes.

`root_duration_us` describes the current admitted inspect, or null if missing.
`first_inspect_root_duration_us` permanently identifies the first inspect in
that helper lease, including when stderr arrives after a second request begins.
The first valid record wins; a missing first result is never replaced by a warm
result. Other actions reset the current value. Request IDs are used internally
for association but are not exported. The nine-digit wire bound is a parser
limit, not a performance threshold. The 32-event and 65,536-byte caps are unchanged.

Use the managed worker's first-inspect scalar in the existing final JSON receipt,
not the tail of its rolling event buffer. A frozen early snapshot may be missing
timing that arrived later. Parent lifecycle stamps record stderr reception time,
not helper execution time, and use a different origin. Do not subtract clocks.

Stop after this one workflow invocation:

- Large helper duration consistent with the parent interval localizes delay inside
  the call, but does not identify managed initialization versus provider/COM delay.
- Short helper duration with a long parent interval leaves surrounding work
  (including timing-wrapper initialization before its first clock read),
  scheduling and receipt delivery open for investigation. It does not by itself
  identify which one caused the gap or justify rewriting provider discovery.
- Missing duration (timeout, exception, diagnostic loss) is inconclusive; do not
  wait longer or infer a numerical call duration.
- A fast pass does not reproduce the failure and is not evidence of a repair.
- Ownership or cleanup failure remains failure. No budgets or checks are relaxed.

Record the exact head, workflow/job/profile, first managed duration or null,
operation timeout/response status, dropped-event/byte counts, and helper/fixture
retirement. Do not publish extra provider content, raw stderr, pixels or IDs.
