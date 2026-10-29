from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class StepInfo(BaseModel):
    label: str
    status: str  # pending / running / done / error / skipped
    detail: str = ""


class CreateTask(BaseModel):
    year_from: int = Field(default=2025, ge=1900, le=2100)
    enable_literature: bool = True
    quick_mode: bool = False
    keywords: list[str] = Field(default_factory=list)
    topic: Optional[str] = None
    search_mode: Literal["keyword", "deep"] = "keyword"
    deep_rounds: int = Field(default=3, ge=1, le=5)

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(word.strip() for word in value if word.strip()))

    @model_validator(mode="after")
    def validate_search_input(self):
        self.topic = (self.topic or "").strip() or None
        if self.search_mode == "deep" and not self.topic:
            raise ValueError("深度检索模式需要提供主题")
        if self.search_mode == "keyword" and not self.keywords:
            raise ValueError("关键词检索需要至少一个非空关键词")
        return self


class PaperInfo(BaseModel):
    title: str = ""
    authors: str = ""
    url: Optional[str] = None
    pdf_url: Optional[str] = None
    paper_id: Optional[str] = None
    summary_cn: Optional[str] = None
    relevance_score: Optional[int] = None
    year: Optional[str] = None
    source: Optional[str] = None
    citations: Optional[int] = None
    relevance_reason: Optional[str] = None
    discovery_round: Optional[int] = None
    relevance_match_scope: Optional[str] = None
    relevance_evidence: Optional[str] = None
    matched_queries: list[str] = Field(default_factory=list)
    citation_from: list[dict] = Field(default_factory=list)


class TaskInfo(BaseModel):
    id: str
    year_from: int = 2025
    status: str  # pending / running / completed / failed / cancelled
    steps: list[StepInfo] = Field(default_factory=list)
    created_at: str = ""
    completed_at: Optional[str] = None
    results: Optional[dict] = None
    error: Optional[str] = None
    quick_mode: bool = False
    keywords: list[str] = Field(default_factory=list)
    papers: list[PaperInfo] = Field(default_factory=list)
    enable_literature: bool = True
    topic: Optional[str] = None
    search_mode: Literal["keyword", "deep"] = "keyword"
    deep_rounds: int = 3


class DownloadPapersRequest(BaseModel):
    paper_indices: list[int] = Field(default_factory=list)


class DownloadHistoryRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list)


class WriteReviewRequest(BaseModel):
    paper_indices: list[int] = Field(default_factory=list)
    instructions: str = ""
    template_id: str = ""
    mode: Literal["survey", "related_work"] = "survey"
    language: Literal["zh", "en"] = "zh"


class ExtractCardsRequest(BaseModel):
    paper_indices: list[int] = Field(default_factory=list)
    force_refresh: bool = False


class CreateTemplateRequest(BaseModel):
    journal_name: str
    papers: list[dict] = Field(default_factory=list)


class UpdateTemplateRequest(BaseModel):
    journal_name: str | None = None
    papers: list[dict] | None = None


class CreatePresetRequest(BaseModel):
    label: str
    content: str


class UpdatePresetRequest(BaseModel):
    label: str | None = None
    content: str | None = None


class TranslateReviewRequest(BaseModel):
    content: str


class ExpandKeywordsRequest(BaseModel):
    keywords: list[str]
