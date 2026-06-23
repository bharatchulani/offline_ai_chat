from pydantic import BaseModel, Field


class Source(BaseModel):
    source: str
    chunk: int
    score: float
    text: str


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]


class IngestResponse(BaseModel):
    source: str
    chunks_added: int


class HealthResponse(BaseModel):
    status: str
    indexed_chunks: int
    chat_model: str
    embedding_model: str

