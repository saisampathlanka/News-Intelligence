"""
NLP processing: entity extraction, sentiment analysis.
Requires: python -m spacy download en_core_web_sm
"""
import json
import logging
from typing import Dict, List, Tuple
from collections import Counter

logger = logging.getLogger("news_intel.nlp")

# Lazy load spaCy (expensive import)
_nlp = None

def get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            _nlp = spacy.load("en_core_web_sm")
            logger.info("spaCy model loaded")
        except OSError:
            logger.error("spaCy model not found. Run: python -m spacy download en_core_web_sm")
            _nlp = None
    return _nlp


def extract_entities(text: str, max_chars: int = 5000) -> Dict[str, List[str]]:
    """
    Extract named entities: people, organizations, locations.
    Returns: {"people": [...], "organizations": [...], "places": [...]}
    """
    nlp = get_nlp()
    if not nlp or not text:
        return {"people": [], "organizations": [], "places": []}
    
    # Truncate to avoid slowdowns
    doc = nlp(text[:max_chars])
    
    people = []
    orgs = []
    places = []
    
    for ent in doc.ents:
        text_clean = ent.text.strip()
        if not text_clean or len(text_clean) < 2:
            continue
            
        if ent.label_ == "PERSON":
            people.append(text_clean)
        elif ent.label_ == "ORG":
            orgs.append(text_clean)
        elif ent.label_ in ("GPE", "LOC"):  # Geo-political entity, location
            places.append(text_clean)
    
    # Deduplicate and keep top mentions
    return {
        "people": _top_mentions(people, limit=10),
        "organizations": _top_mentions(orgs, limit=10),
        "places": _top_mentions(places, limit=10),
    }


def _top_mentions(items: List[str], limit: int = 10) -> List[str]:
    """Return most frequently mentioned entities, preserving order."""
    if not items:
        return []
    counts = Counter(items)
    # Sort by frequency desc, then alphabetically
    sorted_items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [item for item, _ in sorted_items[:limit]]


def extract_keywords(text: str, max_chars: int = 5000, limit: int = 15) -> List[str]:
    """
    Extract important keywords via noun chunks and named entities.
    """
    nlp = get_nlp()
    if not nlp or not text:
        return []
    
    doc = nlp(text[:max_chars])
    keywords = []
    
    # Named entities
    for ent in doc.ents:
        keywords.append(ent.text.strip().lower())
    
    # Noun chunks (excluding stop words)
    for chunk in doc.noun_chunks:
        if not chunk.root.is_stop and len(chunk.text) > 3:
            keywords.append(chunk.text.strip().lower())
    
    return _top_mentions(keywords, limit=limit)


def analyze_sentiment(text: str) -> float:
    """
    Simple sentiment: -1.0 (negative) to +1.0 (positive).
    Uses TextBlob-style polarity via word lists.
    """
    # Lightweight word-based sentiment (no external lib needed)
    positive_words = [
        "good", "great", "excellent", "amazing", "wonderful", "positive",
        "success", "win", "growth", "progress", "hope", "optimistic", "breakthrough",
    ]
    negative_words = [
        "bad", "terrible", "awful", "crisis", "disaster", "fail", "failure",
        "decline", "loss", "negative", "fear", "threat", "tragic", "devastating",
    ]
    
    if not text:
        return 0.0
    
    text_lower = text.lower()
    pos_count = sum(1 for w in positive_words if w in text_lower)
    neg_count = sum(1 for w in negative_words if w in text_lower)
    
    total = pos_count + neg_count
    if total == 0:
        return 0.0
    
    score = (pos_count - neg_count) / total
    return round(max(-1.0, min(1.0, score)), 2)


def process_article_nlp(title: str, description: str, content: str) -> Tuple[Dict, List[str], float]:
    """
    Full NLP processing for an article.
    Returns: (entities_dict, keywords_list, sentiment_score)
    """
    combined_text = f"{title}\n{description}\n{content}"
    
    entities = extract_entities(combined_text)
    keywords = extract_keywords(combined_text)
    sentiment = analyze_sentiment(combined_text)
    
    return entities, keywords, sentiment
