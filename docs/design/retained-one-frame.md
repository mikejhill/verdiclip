# Retained one-frame transport proof

This isolated candidate adds a private pixel sink to the unused helper channel. It does not enable an application capture path or change the one-shot wire protocol.

The worker allocates tightly packed RGB storage only after the existing header and canonical observation are paired. It validates the payload digest and actual binary EOF, then freezes bytes under the original observation deadline. Mutable storage is dropped on the worker before terminal delivery. The immutable candidate is sealed only after the existing control EOF and natural helper cleanup gates succeed.

The terminal signal remains metadata-only. After successful terminal delivery, `HelperChannel.take_frame(generation, request_id)` transfers one immutable `VerifiedFrame`. The frame binds the header, canonical observation, RGB bytes and serialized terminal digest. A second take fails. Cancellation or facade destruction before take invalidates an unclaimed result, including cancellation after terminal delivery. Cancellation after take cannot revoke consumer-owned bytes.

Payload ownership and worker lifetime are separate. Invalidating pixels never releases a live worker or its native resources. The lifetime remains retained across terminal/finished ordering and across facade deletion before its queued terminal slot. Qt `finished` is not evidence of a full native thread join. A missing receipt can become a failure after the existing deadline and independently observed finished event; unresolved native ownership remains retained rather than being declared cleaned up.

The vault mirrors authoritative deadlines stamped by the worker. Startup/session clocks still begin at worker entry. Retirement retains its existing five-second allowance without a new session clamp. Expiry checks use the current phase revision, so an old timer cannot expire a later phase. No payload exists before the first stamp. GUI event-loop suspension can delay physical disposal, but does not permit a late take. There is no new watchdog or hard GUI latency claim.

For an RGB payload of N bytes, the sink reserves at most N mutable bytes and N immutable bytes during freezing. N is at most 1,572,864 bytes. A conservative receive-side accounting also includes a 65,536-byte Qt read buffer, a same-size QByteArray and Python chunk, and the 128-byte header: 2N + 3C + 128. Metadata, allocator overhead, native/kernel buffers and the provider's capture allocation are separate. This is a channel-local bound, not a process RSS or cross-channel budget. After take, consumer-retained aliases are the consumer's responsibility.

Allocation, copying, freezing and hashing run on the worker, outside the GUI-facing vault lock. No raw pixels are queued in signals or receipts. Dropping immutable bytes may still involve allocator work on the releasing thread; no zero-cost GUI disposal claim is made.

Portable tests cover actual frame parsing and EOF failure, cancellation/take ordering, facade destruction, queued receipt/finished ordering, stale timers and failure disposal. Fresh installed-wheel native comparison of the retained bytes with the independent full-panel reference remains required. Existing native digest-only evidence does not qualify this new sink, and the Qt CI/release hold remains independent.

## Installed-wheel proof candidate

The existing owned WPF harness now requests the public frame only after successful terminal delivery and verifies that a second take fails. A separate fixture-only Python thread compares every immutable RGB byte directly to the independent full-panel reference frozen before observation. It also checks the frame/observation/receipt identity and fixed rectangle. The GUI receives only bounded comparison metadata; no image is shown or written by this consumer.

Comparison is logged under the existing three-second call limit and original case deadline. It adds no allowance. The outer owner still supervises the whole executor, including a blocked consumer. The harness requires consumer completion before continuing unchanged retained-process retirement, baseline membership, post-reference equality and natural outer cleanup gates. Failure cannot become success; no consumer timing, cancellation, backpressure or responsiveness qualification is claimed. The pre-existing independent reference images remain the only pixel artifacts.
