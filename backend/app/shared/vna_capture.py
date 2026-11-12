"""Bounded read-only VNA settings carried with captured files and VSWR attempts."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

PositiveNumber = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class CaptureSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: Annotated[int, Field(ge=2, le=100_000, strict=True)] | None = None
    start_hz: PositiveNumber | None = None
    stop_hz: PositiveNumber | None = None
    frequency_step_hz: PositiveNumber | None = None
    if_bandwidth_hz: PositiveNumber | None = None
    averaging_enabled: StrictBool | None = None
    averaging_factor: Annotated[int, Field(ge=1, le=1_000_000, strict=True)] | None = None
    smoothing_enabled: StrictBool | None = None
    smoothing_aperture_percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    correction_enabled: StrictBool | None = None
    correction_status: Literal["NONE", "COR", "C?", "C!", "RC", "RC?", "RC!"] | None = None
    s_parameter: Literal["S11", "S22"] | None = None
    unavailable: Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=20)] | None = None
