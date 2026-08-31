from datetime import datetime
from enum import Enum
from pydantic import BaseModel


class ExactTime(BaseModel):
    kind: str = "exact"
    value: datetime


class TimeRange(BaseModel):
    kind: str = "range"
    start: datetime
    end: datetime


class ApproximateTime(BaseModel):
    kind: str = "approximate"
    raw_expression: str


class RelativeRelation(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    DURING = "during"
    OVERLAPS = "overlaps"


class RelativeTime(BaseModel):
    kind: str = "relative"
    relation: RelativeRelation
    reference_id: str

