from pydantic import BaseModel, Field


class L3ClassificationResult(BaseModel):
    bucket_name: str = Field(description="The name of the single best-matching bucket.")
    reason: str = Field(description="One sentence explaining why this bucket was chosen.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this classification, 0-1.")


class SubjectExtractionResult(BaseModel):
    subject_name: str | None = Field(
        default=None,
        description=(
            "The name of the specific person this message is about, if any. "
            "None if the message isn't about any particular person."
        ),
    )
    reason: str = Field(description="One sentence explaining the extraction (or lack of a subject).")
