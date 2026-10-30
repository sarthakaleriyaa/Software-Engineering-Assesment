
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    # Omit (null) to search all ready documents; otherwise only these documents.
    document_ids: list[int] | None = Field(default=None, max_length=100)


class Citation(BaseModel):
    source_id: str | None = None
    document_id: int
    filename: str
    chunk_index: int
    page_number: int | None = None
    passage: str
    score: float | None = None


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    estimated_cost_usd: float | None = None


class ChatResponse(BaseModel):
    question_id: int | None = None
    answer: str
    refused: bool = False
    citations: list[Citation]
    usage: Usage = Usage()


class QuestionHistoryItem(BaseModel):
    id: int
    question: str
    answer: str | None
    status: str
    document_ids: list[int] | None = None
    citations: list[Citation] | None = None
    usage: Usage
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
