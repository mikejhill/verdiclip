"""One bounded storage job; workers own files, never GUI or session callbacks."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from time import monotonic

from verdiclip.capture.scroll.storage import AppendRequest, StagedAppend, StripStore
from verdiclip.exceptions import CaptureError


@dataclass(frozen=True, slots=True)
class StorageReceipt:
    """A staged transaction or exact owners whose cleanup still needs retry."""

    staged: StagedAppend | None = None
    remaining: tuple[StagedAppend, ...] = ()
    error: str = ""


class StorageJob:
    """Run exactly one operation, with no queue and no Qt-owned thread lifetime.

    The thread retains this object until all I/O returns. Discarded staging and
    transferred cleanup finish even if the GUI owner is destroyed on shutdown.
    Callers retain failed receipts and cannot replace this job before it finishes.
    """

    def __init__(
        self,
        request: AppendRequest | None = None,
        *,
        cleanup: tuple[StagedAppend, ...] = (),
    ) -> None:
        """Transfer only immutable requests and private directory owners."""
        if request is not None and cleanup:
            raise ValueError("A storage job cannot stage and clean up simultaneously")
        self._request = request
        self._cleanup = cleanup
        self._lock = threading.Lock()
        self._discard = False
        self._done = threading.Event()
        self._receipt: StorageReceipt | None = None
        self._owned: tuple[StagedAppend, ...] = ()
        self._thread = threading.Thread(
            target=self._run, name="VerdiClip capture storage", daemon=True
        )

    def start(self) -> None:
        """Start only after the binding retains this exact potentially live owner."""
        self._thread.start()

    @property
    def done(self) -> bool:
        """A receipt becomes visible only after its last file operation."""
        return self._done.is_set() and not self._thread.is_alive()

    def wait(self, seconds: float) -> bool:
        """Allow a bounded shutdown wait without requiring any GUI event delivery."""
        deadline = monotonic() + max(0.0, seconds)
        if self._thread.ident is None:
            self._done.wait(max(0.0, deadline - monotonic()))
        if self._thread.ident is not None:
            self._thread.join(max(0.0, deadline - monotonic()))
        return self.done

    def discard(self, cleanup: tuple[StagedAppend, ...] = ()) -> bool:
        """Arrange worker-side retirement; False means its receipt is already ready."""
        with self._lock:
            if self._done.is_set():
                return False
            self._discard = True
            self._cleanup += cleanup
            return True

    def take(self) -> StorageReceipt:
        """Transfer the finished receipt exactly once on the owning GUI thread."""
        with self._lock:
            if not self.done or self._receipt is None:
                raise CaptureError("Capture storage has not finished")
            receipt, self._receipt = self._receipt, None
            return receipt

    @staticmethod
    def _retire(transactions: tuple[StagedAppend, ...]) -> StorageReceipt:
        remaining: list[StagedAppend] = []
        errors: list[str] = []
        for transaction in transactions:
            try:
                transaction.close()
            except Exception as error:  # noqa: BLE001 - retain every unconfirmed directory owner
                remaining.append(transaction)
                errors.append(str(error) or type(error).__name__)
        return StorageReceipt(remaining=tuple(remaining), error="; ".join(errors))

    def _run(self) -> None:
        """Always return a receipt, including unexpected worker failures."""
        try:
            self._work()
        except BaseException as error:  # noqa: BLE001 - never strand cleanup owners
            with self._lock:
                self._receipt = StorageReceipt(
                    remaining=self._owned + self._cleanup,
                    error="Capture storage worker failed: "
                    + (str(error) or type(error).__name__),
                )
                self._cleanup = ()
                self._done.set()
        finally:
            self._request = None
            self._owned = ()

    def _work(self) -> None:
        staged = StripStore.stage(self._request) if self._request is not None else None
        if (
            staged is not None
            and staged.error is not None
            and not isinstance(staged.error, CaptureError)
        ):
            staged = replace(
                staged,
                error=CaptureError(
                    f"Capture storage worker interrupted: {type(staged.error).__name__}"
                ),
            )
        self._request = None  # Never retain every full viewport after admission.
        with self._lock:
            if staged is not None and not self._discard:
                self._receipt = StorageReceipt(staged=staged)
                self._done.set()
                return
            cleanup = self._cleanup + ((staged,) if staged is not None else ())
            self._cleanup = ()
        self._owned = cleanup
        receipt = self._retire(cleanup)
        # Cancellation can add committed directories while cleanup is running.
        while True:
            with self._lock:
                extra = self._take_cleanup()
                if not extra:
                    self._receipt = receipt
                    self._done.set()
                    return
            self._owned = receipt.remaining + extra
            additional = self._retire(extra)
            receipt = StorageReceipt(
                remaining=receipt.remaining + additional.remaining,
                error="; ".join(filter(None, (receipt.error, additional.error))),
            )

    def _take_cleanup(self) -> tuple[StagedAppend, ...]:
        """Read cancellation transfers under the caller's held lock."""
        cleanup, self._cleanup = self._cleanup, ()
        return cleanup
