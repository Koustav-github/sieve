from typing import Any

ADDRESS_KEYS = ("address", "email", "username")
CONVERSATION_ID_KEYS = ("conversation_id", "id")


def resolve_identity_address(connection: dict) -> str:
    """Extract the contact address (recipient) from a connection dict
    returned by `register_identities()` for an email channel. See this
    module's docstring note in the implementation plan re: the exact key
    not being live-verified - tries the plausible candidates in order and
    raises loudly on a shape mismatch instead of silently sending to an
    empty recipient.

    This is only ever called for email connections (relay dispatch always
    goes out over email), so a found value is additionally required to look
    like an email address (contains "@") before it's returned - a bare
    "username" value (no "@") is rejected and the remaining candidate keys
    are tried instead, so we never silently send to an obviously-invalid
    recipient."""
    rejected: list[tuple[str, Any]] = []
    for key in ADDRESS_KEYS:
        value = connection.get(key)
        if not value:
            continue
        if "@" in value:
            return value
        rejected.append((key, value))
    raise KeyError(
        f"connection dict has none of {ADDRESS_KEYS} containing '@' "
        f"(rejected non-email candidates: {rejected!r}): {connection!r}"
    )


def send_relay(client: Any, *, connection_id: str, recipient: str, text: str) -> str:
    """Cold-starts a new conversation with the target identity's own
    registered address, carrying the extracted relay message. Returns the
    new conversation's id, extracted from Caspian's response (same
    key-shape caveat as `resolve_identity_address`)."""
    response = client.initiate(connection_id, recipient, text)
    for key in CONVERSATION_ID_KEYS:
        value = response.get(key)
        if value:
            return value
    raise KeyError(f"initiate() response has none of {CONVERSATION_ID_KEYS}: {response!r}")


def deliver_reply(client: Any, *, caspian_message_id: str, text: str) -> dict:
    """Replies on the channel the original relay request arrived on,
    delivering the target identity's reply (or an error explanation) back
    to the requester."""
    return client.reply(caspian_message_id, text=text)
