# Native unresolved initial hit candidate

This isolated candidate starts from PR97 (3168b17), not the diagnostic branch.
The independently reviewed qapp test initialization correction is also included.
It remains unqualified until Windows CI and all outstanding release gates pass.

## Evidence and scope

PR99 showed an empty method runtime ID and null runtime property for the owned
WinForms first hit, while its window root and the WPF hit/root had valid IDs.
PR100 then verified that the WinForms first hit was a non-scrollable list item,
with a two-parent identified suffix reaching the exact owned root. No property
fallback or independent CompareElements identity oracle was established.

Only a null/empty runtime ID at depth0 may use this candidate's transient proof.
Oversized IDs, provider exceptions, later missing IDs, wrong process, invalid
control types, scroll-pattern availability, scrollbar/window roles, malformed
bounds or a point outside the inset bounds all refuse. Shared pure
TransientHitPolicy enforces this proof in discovery and native mutation.
There is no synthesized runtime ID, retry, focus change, alternate query,
threshold relaxation, or permission inferred from a transient hit.

## Explicit representation

Ordinary discovery retains its existing hit_identity and node chain, with an
optional null unresolved_hit field. An unresolved initial OTHER leaf uses:

- hit_identity: null
- unresolved_hit: kind other, original physical bounds, exact window identity,
  parent_identity equal to nodes[0].identity, vertically_scrollable false
- nodes: the fully identified, contiguous nearest-first parent suffix

The unresolved object has no identity or reference field and cannot be a saved,
selected, observed, sampled or mutated target. The two hit representations are
exclusive. Decoder validation rejects extra unresolved fields, conflicting IDs,
missing proof, candidate claims, wrong links/owners, invalid bounds and suffixes
longer than31. Every suffix node still has a strict unique identity and the chain
must end at the verified window root. The missing leaf consumes the first of
32 total traversal slots; it is never removed from the budget.

SemanticTargets receives the unresolved link separately. It checks the actual
leaf's point and window, then follows its explicit OTHER parent link with parent
point/window validation. A normal list-item→content hit remains WINDOW. An
OTHER→verified-scrollbar chain retains existing scrollbar semantics; explicit
mode offers only identified scrollable suffix nodes. The parent is never labeled
as the actual hit, and no pseudo-node ID is introduced.

## All resolver paths

Discovery FindAncestors resets request-local unresolved state at entry and
publishes its bounds only after complete root validation. Inspect emits the
explicit representation. Observe and sample traverse the same policy but select
only the strict saved runtime ID from the identified suffix. Image consent,
destination, viewport, release and DPI-before-result guards are unchanged.

Native step and restore use the injectable NativeIdentityPath traversal over the
same initial-only proof. They require typed same-process observations, valid IDs
for all later nodes, no duplicate IDs, the exact root ID and HWND, and a matching
strict saved selected ID before returning a target. Existing target generation,
geometry, metric, visibility/occlusion and no-focus mutation checks remain. A
transient leaf cannot become the target. New proof VARIANT outputs are owned in
finally, including COM output on a failed call; raw parent acquisitions likewise
remain owned when interop throws.

Only missing-leaf proof adds control-type, ScrollPattern-availability and bounds
queries. Mutation also verifies root HWND explicitly. Every call stays within
unchanged worker/operation deadlines and32-node bound. These calls may affect
performance; automated timing and physical/cursor holds are not satisfied by
source review or portable tests.

## Validation

Portable protocol and semantic tests cover hostile representations, identity-free
reference refusal,31/32 suffix boundary, OTHER→scrollbar and list-item→content
normal/explicit behavior, and original/parent point/owner checks. Windows C# tests
exercise actual discovery and injected mutation traversal, null/empty cases,
provider faults, candidate rejection, second missing node, cycles, root/target
changes,32/33 total-node limits, and stale unresolved state after a failed or
later identified traversal. Actual PowerShell function tests exercise inspect,
observe and sample with an unresolved leaf and a strict identified target.
Existing mutation-policy tests retain step/restore authorization and cleanup
failure coverage. Windows compiler/fake and real hosted fixtures remain mandatory.
