def escape_for_message_block(text: str) -> str:
    """Strips the closing tag of the `<message>...</message>` untrusted-
    content wrapper used by both relay pipelines' prompts, so a sender
    can't close the block early and have the remainder of their text read
    as instructions by the LLM."""
    return text.replace("</message>", "")
