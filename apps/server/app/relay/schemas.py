from pydantic import BaseModel, Field


class RelayExtractionResult(BaseModel):
    is_relay_request: bool = Field(
        description=(
            "True if the message explicitly asks to reach a different "
            "registered identity (e.g. '@bot let internal know...'). False "
            "for a plain message that isn't asking to be relayed anywhere."
        )
    )
    target_identity: str | None = Field(
        default=None,
        description=(
            "One of 'careers', 'support', 'internal' - which identity the "
            "sender wants this relayed to. None if is_relay_request is False."
        ),
    )
    message_text: str | None = Field(
        default=None,
        description=(
            "The message to relay to the target identity, extracted from "
            "the sender's request. None if is_relay_request is False."
        ),
    )
    claims_employee: bool = Field(
        default=False,
        description="True if the sender explicitly claims to be an employee.",
    )
    employment_id: str | None = Field(
        default=None,
        description="The employment ID the sender supplied, if any.",
    )
