from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawArticle:
    """Normalized in-memory article before DB write."""
    url: str
    title: str
    description: str
    content: str
    source_name: str
    source_type: str                       # rss | newsapi | guardian | gnews
    published_at: Optional[datetime]
    author: Optional[str] = None
    language: str = "en"

    def is_valid(self, min_words: int = 20) -> bool:
        text = (self.title or "") + " " + (self.description or "")
        return bool(self.url) and bool(self.title) and len(text.split()) >= min_words
