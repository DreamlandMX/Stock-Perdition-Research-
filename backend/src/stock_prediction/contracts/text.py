"""Language-neutral references used only to render system-owned display text."""

from pydantic import BaseModel, ConfigDict, Field


class LocalizableText(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", allow_inf_nan=False
    )

    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z][a-zA-Z0-9_.]*$")
    params: dict[str, str | int | float] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
