# Independent retained-link diagnostic

`scripts/scrolling_link_probe.py` is hosted live-harness instrumentation, not a
production matcher, registration fallback, or qualification oracle. It does not
change the product result. `product-capture-result.json` preserves that result,
its restoration status, and the two guarded post-capture reads before any long
diagnostic starts. Raw `before.png` and `after.png` remain separate evidence.

## Admission and acquisition

The live harness admits this probe only after product restoration is reported,
both guarded native reads equal the original state, and raw before/after images
are identical. The final two retained frame states specify the anchor/trial;
the anchor must also appear earlier in the trace. The complete trace must have
unchanged H/view extent, and the final forward displacement must be positive and
integral in native viewport pixels. This does not infer matcher acceptance from
the trace. An unsuitable pair produces an explicit failed diagnostic receipt.

The probe rechecks the retained Document and native point identity, original
viewport, input ownership, focus and extent. It issues one move to A and one to B.
Every movement requires two identical reads of the exact requested H/V/view
state from the original retained ScrollPattern. Eight unacknowledged samples
allow one read-only native refresh, never a repeated setter or borrowed state.
An unresolved write prohibits restoration and subsequent broad diagnostics.

## Links and evidence

At A and B a complete raw subtree walk is required (1200 nodes, depth 48). Every
Hyperlink interface is retained across the movement. No best pair is selected.
The receipt retains all unqualified links and reasons, all paired motion,
stationary/conflicting links, new links, and lost/duplicate identities. Missing
identities and newly eligible unknown links prevent a complete-witness claim,
even if the raw paired statistics contain a favorable pair.

Eligibility requires positive, fully contained bounds, a visible Hyperlink role,
a retained native ancestor chain to the original Document, no intervening
Document or scroll provider, stable bounds, and a native center hit whose link
ancestor is CompareElements-identical. Ancestry itself is compared through
retained interfaces. Runtime IDs and pointer addresses do not establish identity.
A center hit confirms the center only: it does not prove the entire rectangle is
unoccluded or owns every painted pixel.

Two distinct nonoverlapping rectangles in both observations and unanimous
positive integral vertical displacement among every paired eligible link are
reported as an observed motion witness. X and dimensions must remain unchanged.
This is observational evidence, never product acceptance or Google DOM ground
truth. A raw frame before and after each observation phase must be identical;
frame/provider state is checked on both sides. Changes invalidate the observation.

## Limits and restoration

70 seconds is an admission budget for observations, with a separate 10-second
restoration admission budget. Neither can interrupt a blocked COM call. The
existing four-minute GitHub Actions workflow step is the only hard kill bound;
it covers setup, product capture, this probe, and the broad diagnostic together.
A native call can overrun local budgets. A forced workflow termination may leave
restoration unknown. No claim of a local hard watchdog is made.

Checkpoint receipts distinguish started, pending moves, enumeration/observation,
and pending restoration. Pending/unknown probe restoration must never be confused
with the product's earlier restored status. On ordinary completion, restoration
requires resolved ownership/state, one original-position move if needed, exact
native state and exact original pixels. A failed or uncertain restoration blocks
all subsequent diagnostic writes. Detailed elapsed time and guarded-native-call
count expose instrumentation cost; incomplete traversal is not complete evidence.
