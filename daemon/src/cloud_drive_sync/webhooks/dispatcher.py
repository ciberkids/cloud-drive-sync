"""Bridge between the event bus and delivery.

One subscriber on the bus. For each event it translates the internal name to the
public one, works out which pair it belongs to, resolves that pair's targets, and
hands the matching ones to :class:`~.delivery.WebhookDelivery`.

Everything here must be cheap. It runs inline on whatever coroutine emitted the
event -- usually mid-sync-pass -- so it does no serialisation, no signing and no I/O.
Resolution is pure dict work over already-parsed config.
"""

from __future__ import annotations

from cloud_drive_sync.config import Config
from cloud_drive_sync.util.logging import get_logger
from cloud_drive_sync.webhooks.delivery import WebhookDelivery
from cloud_drive_sync.webhooks.payload import (
    NON_PAIR_EVENTS,
    make_event,
    public_name,
)
from cloud_drive_sync.webhooks.resolver import SCOPE_GLOBAL, resolve_targets

log = get_logger("webhooks.dispatcher")


class WebhookDispatcher:
    """Subscribes to the event bus and routes events to their targets."""

    def __init__(self, config: Config, delivery: WebhookDelivery) -> None:
        self._config = config
        self._delivery = delivery
        # Problems are reported once per distinct message. Resolution runs per event,
        # so logging every time would repeat the same misconfiguration thousands of
        # times and bury everything else.
        self._reported: set[str] = set()

    async def __call__(self, event: str, params: dict) -> None:
        """The bus subscriber.

        Never raises: the bus isolates subscribers anyway, but a webhook feature that
        relies on someone else's guard to avoid breaking a sync is one refactor away
        from breaking it.
        """
        try:
            self._dispatch(event, params)
        except Exception:
            log.exception("Webhook dispatch failed for %r", event)

    def _dispatch(self, event: str, params: dict) -> None:
        name = public_name(event)
        if name is None:
            return  # not part of the public vocabulary

        pair_id = params.get("pair_id")
        pair = self._find_pair(pair_id) if pair_id else None

        levels: list[tuple[str, object]] = [(SCOPE_GLOBAL, self._config.webhooks)]
        if pair is not None and name not in NON_PAIR_EVENTS:
            levels.append((f"pair:{pair.uid}", pair.webhooks))

        targets, problems = resolve_targets(levels)  # type: ignore[arg-type]
        for problem in problems:
            if problem not in self._reported:
                self._reported.add(problem)
                log.error("Webhook configuration problem: %s", problem)

        matching = [t for t in targets if t.matches(name)]
        if not matching:
            return

        scope = self._build_scope(pair, params)
        raw = make_event(name, params)
        for target in matching:
            self._delivery.submit(raw, target, scope)

    def _find_pair(self, pair_id: str):
        """Map the engine's positional ``pair_N`` to its configured pair.

        The positional id is what the engine emits; it must not leave the process, so
        it is translated here and only the pair's ``uid`` goes into the payload.
        """
        if not pair_id.startswith("pair_"):
            return None
        try:
            index = int(pair_id.removeprefix("pair_"))
        except ValueError:
            return None
        pairs = self._config.sync.pairs
        return pairs[index] if 0 <= index < len(pairs) else None

    def _build_scope(self, pair, params: dict) -> dict:
        if pair is None:
            return {}
        account = next(
            (
                a
                for a in self._config.accounts
                if a.email == pair.account_id and a.provider == (pair.provider or "gdrive")
            ),
            None,
        ) or next((a for a in self._config.accounts if a.email == pair.account_id), None)
        return {
            # The stable uid, never `pair_N`.
            "pair_id": pair.uid,
            "pair_label": params.get("pair_label") or pair.local_path,
            "account": {
                "provider": pair.provider or "gdrive",
                "email": account.email if account else pair.account_id,
            },
            "local_path": pair.local_path,
            "remote_folder_id": pair.remote_folder_id,
        }
