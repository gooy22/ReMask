from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal['QUEUED','RUNNING','SUCCESS','FAILED','PARTIAL']
TaskStatus = Literal['QUEUED','RUNNING','SUCCESS','FAILED']

class TaskInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=160)

class ProfileJobInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: str = Field(min_length=1, max_length=160)
    tasks: list[TaskInput] = Field(min_length=1, max_length=100)

class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profiles: list[ProfileJobInput] = Field(min_length=1, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=160)

class JobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    items_total: int

class RetryResponse(BaseModel):
    job_id: str
    requeued: int

class HealthResponse(BaseModel):
    ok: bool
    service: str
    queued_items: int
    worker_concurrency: int
