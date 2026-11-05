# Single reviewed-capture contract proposal

Status: accepted by parent and implemented by the scrolling owner in
`f25091d7af711d89db5a6e5d30f569a7f89fed16`. The tabs worker imported that exact
contract and its foundation via provenance-preserving cherry-picks, removed its
duplicate class, and migrated controller/tests. The proposal below records the
agreed design. Only pixel-free CaptureProvenance remains document-owned.

Keep the only `ReviewedCapture` class in `verdiclip.capture.reviewed`. Retain
`CaptureKind.SCREENSHOT="screenshot"` and `SCROLLING="scrolling"`. Replace the
completeness values with `COMPLETE="complete"` and `PARTIAL="partial"`.

```python
@dataclass(frozen=True, slots=True)
class ReviewedCapture:
    image: QImage
    title: str
    created_at: datetime
    source_kind: CaptureKind
    completeness: CaptureCompleteness
    stop_reason: str | None = None
    capture_id: str = field(default_factory=lambda: uuid4().hex, kw_only=True)
```

Require non-null image; aware created_at (utcoffset is not None); nonempty capture_id;
actual CaptureKind and CaptureCompleteness enum members; optional nonempty stop_reason
when supplied. Retain QImage(image) so producer/consumer mutations detach. Do not
silently repair invalid inputs. Completeness is required, avoiding accidental
complete marking. A fresh capture ID is minted once when the reviewed image is
accepted, then the same value is reused on delivery retry/duplicate callback.
Separate capture operations get separate IDs even with identical pixels.

COMPLETE means the selected/requested capture extent is fulfilled. It is never a
claim that an entire web page or infinite document was acquired. PARTIAL means the
accepted image ended short or uncertain within that intended extent, after review.
Keep scrolling stop evidence in stop_reason, using the existing StopReason.value
(e.g. reached_reported_end). Preserve its current explicit reason on migration;
SELECTED_RANGE maps to COMPLETE only when the selected extent was fulfilled. A
reported end maps to COMPLETE for the selected run, with reached_reported_end
retained. Partial remains PARTIAL with its reason. The scrolling plan stays private.

Tabs migration, now implemented by this worker:

- Delete document.admission.ReviewedCapture, import the single canonical class.
- Retain CaptureProvenance only as pixel-free document metadata, projecting
  capture_id, created_at, title, source kind, completeness and optional stop reason.
- Change controller retry/receipt keys to capture_id, output naming time to
  created_at, ordinary captures to SCREENSHOT/COMPLETE. Keep ordinary capture
  subtype/repeat plans outside the shared value.
- Preserve the exact accepted result on failed-sink retry. Do not adapt by
  constructing a second reviewed value on every delivery call.
- Preserve independent output choices, background focus, successful receipts,
  stale-output guards and pending-delivery close/update blocks.

Scrolling migration is owned by its worker: canonical module, ScrollSession.accept,
producer metadata and current capture-reviewed/scroll-core tests. Session.accept
must reuse its accepted result on repeated acceptance so identity remains stable.
No tabs code imports the scrolling session, tiles, plan or experimental input backend.

Migration tests (offscreen/model): canonical constructor rejects null/naive/invalid
inputs; COW image isolation both directions; timezone/stop evidence retained;
ordinary and scrolling production return the same canonical class; repeated accept
and repeated delivery preserve capture_id; one callback twice produces one file,
copy and tab; two equal-pixel captures with distinct IDs remain two documents;
failed save/copy retries use original ID and skip successes; newly edited tab stays
dirty after original-image retry; COMPLETE/PARTIAL and optional stop_reason survive
window moves; positional constructor migration cannot swap timestamp/source fields.

This proposal introduces no second class and does not alter capture input. Native
UI/input testing and merge/release remain held for combined qualification and the
user's local verification before merge. No tabs push is authorized by the separate
scrolling draft-push approval.
