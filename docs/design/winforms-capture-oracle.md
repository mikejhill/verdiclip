# WinForms positive capture and unsupported negative control

The first PR101 WinForms run delivered output without product errors, then failed
an obsolete provider-name-based expectation of unsupported behavior. Its receipt
reported no subsequent captures and never executed the positive pixel/completeness
oracle. It is evidence of progress, not retroactive qualification.

Default WPF and WinForms scrolling fixtures now run the same positive verifier:
COMPLETE scrolling provenance, real scrollbar/child ancestry, real ScrollPattern
references, exact image dimensions and every RGBA pixel, deferred-storage checks,
input/popup/cleanup, fixture end_seen and restored offset0, and two subsequent
real active-window captures with distinct documents and selected tabs. The
WinForms bitmap remains384×1120, rendered as140 owner-drawn8-pixel rows into a
384×280 viewport. TopIndex*8 independently records offset and end>=840. The
existing render callback, bitmap bytes, pixel tolerance (none), and timing
budgets are unchanged. Explicit unresolved-hit metadata is passed to the
semantic oracle. Positive stage flags are written only after their assertions.

An additional declared `unsupported-scrollbar` WinForms fixture uses a PictureBox
and standalone VScrollBar with no scrollable container. It does not depend on
missing runtime IDs or artificially modifying client discovery. Its actual UIA
discovery must show no eligible ScrollPattern ancestor; unexpected support or
helper failure fails the negative test. The existing safe-refusal verifier still
requires no output, step, restore, sample or storage work, unchanged popup and
input cleanup, and offset0/end_seenfalse. Offset tracks standalone scrollbar
Value*8, so unintended movement is not hidden. Scroll commands are prohibited in
this mode. This negative lane makes no pixel capture claim.

The fixture mode is explicit, validated before native setup, sent to the owned
fixture, attested in its nonce/owner-bound state and named in its receipt.
Unknown modes/providers and WPF-negative combinations are rejected. Expected
results derive from the declared mode, never from provider name or actual output.
The original five positive CI profiles remain; one distinct negative WinForms
profile is added with identical execution/input safety gates and budgets.

Portable fault tests execute the actual positive verifier and reject errors,
missing output, incorrect kind/completeness, dimensions/pixels, missing references,
unverified hit, storage/input/popup/cleanup failure, incomplete end/restoration,
and insufficient repeated captures. Existing negative-verifier fault tests remain
active. Windows parser/draw tests and real positive/negative hosted runs remain
required; portable success cannot establish native pixel correctness.
