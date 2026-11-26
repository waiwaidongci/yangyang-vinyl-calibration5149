from pydantic import BaseModel, ConfigDict, Field, constr
from typing import Annotated


class VersionDatasetImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Annotated[str, constr(strip_whitespace=True, min_length=2, max_length=140)]
    csv: str = Field(min_length=5, max_length=600_000)


class VersionBatchCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    label: str = Field(min_length=2, max_length=140)
    dataset_id: int = Field(gt=0)
    holdout_fraction: float = Field(default=0.25, ge=0.15, le=0.40)


class LabelWithdrawal(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    clip_ids: list[str] = Field(min_length=1, max_length=500)
    reason: Annotated[str, constr(strip_whitespace=True, min_length=4, max_length=500)]


class VersionSignoffCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    decision: str = Field(pattern='^(approved|rejected)$')
    note: str = Field(min_length=4, max_length=1000)


class SignoffInfo(BaseModel):
    id: int
    batch_id: int
    decision: str
    note: str
    status: str
    created_at: str
    superseded_at: str | None
    snapshot: dict


class BatchInfo(BaseModel):
    id: int
    label: str
    dataset_id: int
    created_at: str
    status: str
    holdout_fraction: float
    withdrawn_ids: list
    superseded_by: int | None
    recompute_reason: str | None
    recommendation: str | None
    signoff: SignoffInfo | None
