"""Debounce buffer for inbound WhatsApp messages (item 216).

People text the way they speak: "Hola" / "busco piso" / "en Alicante" in
three seconds. One agent run per message means three overlapping replies —
the clearest possible tell that there is a robot on the other side — and
three times the token bill.

So an inbound message is not answered immediately: it is appended to
`conversation.extra["pending_buffer"]` and the run waits
`DEBOUNCE_SECONDS`. Whoever appended *last* is the only one that runs, and
it runs over the whole buffer joined into a single turn.

State lives in the conversation row rather than Redis on purpose: the queue
broker is already the weakest link in this stack and a second one buys
nothing here. The trade-off is written down in the module that uses it
(app/api/whatsapp.py).

Everything in here is pure: dicts in, dicts out. `extra` is never mutated —
callers reassign `conv.extra` so SQLAlchemy's change tracking notices, the
same convention `Lead.extra` and `agency.settings` follow.
"""
from __future__ import annotations

from typing import Any

# 12 s: long enough to catch the "Hola" / "busco piso" burst, short enough
# that a lead who wrote one sentence does not feel ignored.
DEFAULT_DEBOUNCE_SECONDS = 12.0

PENDING_BUFFER_KEY = "pending_buffer"
SEEN_IDS_KEY = "recent_inbound_ids"

# How many already-processed provider ids to remember per conversation. They
# are the second line of the dedup defence (app/channels/dedup.py) for the
# window in which a message is buffered but not yet stored as a Message row.
MAX_SEEN_IDS = 50


def _items(buffer: Any) -> list[dict]:
    if not isinstance(buffer, dict):
        return []
    return [dict(i) for i in (buffer.get("items") or []) if isinstance(i, dict)]


def append_to_buffer(
    extra: dict | None, *, external_message_id: str | None, text: str
) -> tuple[dict, int | None]:
    """Add a message to the conversation's pending buffer.

    Returns `(new_extra, seq)`. `seq` is this message's place in the buffer
    and identifies the run that owns it; `None` means the message was
    already buffered or already processed, i.e. a duplicate that must be
    dropped without side effects.
    """
    new = dict(extra or {})
    buffer = new.get(PENDING_BUFFER_KEY)
    items = _items(buffer)
    seen = [str(x) for x in (new.get(SEEN_IDS_KEY) or [])]

    if external_message_id:
        if external_message_id in seen:
            return new, None
        if any(i.get("id") == external_message_id for i in items):
            return new, None

    seq = int((buffer or {}).get("seq") or 0) + 1 if isinstance(buffer, dict) else 1
    items.append({"id": external_message_id, "text": text})
    new[PENDING_BUFFER_KEY] = {"seq": seq, "items": items}
    return new, seq


def buffer_is_current(extra: dict | None, seq: int) -> bool:
    """True when `seq` is still the last message appended.

    False means a newer message landed while we slept: that run will process
    the whole buffer, including ours, so this one must stay quiet.
    """
    buffer = (extra or {}).get(PENDING_BUFFER_KEY)
    if not isinstance(buffer, dict):
        return False
    return int(buffer.get("seq") or 0) == seq


def take_buffer(extra: dict | None) -> tuple[dict, list[str]]:
    """Empty the buffer and return `(new_extra, texts)` in arrival order.

    The provider ids of what we take are remembered in `recent_inbound_ids`
    so a retry arriving after the buffer was drained — but before the agent
    run stored its Message row — is still recognised as a duplicate.
    """
    new = dict(extra or {})
    buffer = new.pop(PENDING_BUFFER_KEY, None)
    items = _items(buffer)

    texts = [str(i.get("text") or "") for i in items]
    texts = [t for t in texts if t.strip()]

    seen = [str(x) for x in (new.get(SEEN_IDS_KEY) or [])]
    for item in items:
        message_id = item.get("id")
        if message_id and str(message_id) not in seen:
            seen.append(str(message_id))
    new[SEEN_IDS_KEY] = seen[-MAX_SEEN_IDS:]
    return new, texts


def merge_texts(texts: list[str]) -> str:
    """The buffered burst as one turn for the agent.

    Newlines, not spaces: "Hola\\nbusco piso\\nen Alicante" reads as three
    things the person said, which is what it is.
    """
    return "\n".join(t.strip() for t in texts if t.strip())
