from typing import Dict, List, Optional, TypedDict


class ResearchState(TypedDict):
    keywords: List[str]
    literature_review: Optional[str]
    papers: Optional[List[Dict]]
    representative_papers: Optional[List[Dict]]
    execution_log: Optional[List[str]]
    screening: Optional[Dict]
    search_diagnostics: Optional[List[Dict]]
    search_warnings: Optional[List[str]]
    year_from: int
