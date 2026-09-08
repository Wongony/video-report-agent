from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AsrSegment(BaseModel):
    """An untouched ASR segment normalized only into millisecond boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=1)
    text: str
    words: list[dict] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ASR segment text must not be blank")
        return value

    @model_validator(mode="after")
    def end_must_follow_start(self) -> "AsrSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError("ASR segment end_ms must be greater than start_ms")
        return self
