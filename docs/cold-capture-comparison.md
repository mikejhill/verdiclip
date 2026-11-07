# Cold capture baseline comparison

The newly added paired cold jobs are informational comparisons against exact
0.12.1 source (`a1c6bbe488284cf5f5b4ed7a8b1e30181e0f62ed`), not a demonstrated
regression gate. They live in `tabs-cold-diagnostics.yml`. Failed assertions still
fail their jobs: there is no `continue-on-error`, xfail, or increased threshold.
The original aggregate 150 ms assertion remains unchanged. The separate
`tabs-baseline-native.yml` still enforces capture-subtree identity, native capture,
and actual editor-window focus/tab destination checks on both Qt versions.

## Retained observations

Each row is one fresh hosted Windows job. Values are milliseconds; paired jobs
use the same locked Python environment and selected Qt, not the historical
shipped executable/runtime. Neither run replaces the other's evidence.

| Run / Qt | Native first region paint candidate / 0.12.1 | Synthetic cold candidate / 0.12.1 |
| --- | --- | --- |
| [42335666975](https://github.com/mikejhill/verdiclip/actions/runs/42335666975) / 6.11.2 | 466.126 / 326.253 | 1965.868 / 2461.2565 |
| Same run / 6.12.0 | 379.615 / 346.813 | 2053.3769 / 2026.2085 |
| [42336059343](https://github.com/mikejhill/verdiclip/actions/runs/42336059343) / 6.11.2 | 412.401 / 841.177 | 3099.1105 / 1955.2674 |
| Same run / 6.12.0 | 388.181 / 378.941 | 2464.7357 / 2238.9468 |

The second run was triggered automatically by the test-isolation correction.
It is not a warm retry or a reason to select the fastest result. Earlier cold
failures also remain part of the record.

All eight native first-paint observations confirmed cleanup, one overlay, no
prior widget paint, no injected input, and **false** `within_original_150ms`.
Their successful job status means evidence collection succeeded, not that the
150 ms budget passed. The metric ends after the first completed paint of every
created overlay, including the real screen freeze; QApplication/service
construction intentionally occurs before the clock. This is not a physical
input-to-display or compositor measurement.

Every synthetic job retained the unchanged 150 ms failure and 29 passing tests.
Each functional native job passed all 18 tests on both runs, with actual OS and
Qt editor focus plus new-tab selection receipts. Those window/fullscreen/repeat
measurements are not cold region measurements.

Both exact released source and candidate exceeded the cold budget. These few
hosted observations do not establish statistical equivalence, a regression's
cause, or fixed latency. No product capture code or original acceptance
assertion was changed to obtain this classification. Release decisions must
consider the original aggregate tests and the independent functional gates;
the informational workflow does not make its red results disappear.
