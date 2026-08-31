from pydantic import BaseModel, Field


class Case(BaseModel):
    id: str
    title: str
    description: str | None = None

