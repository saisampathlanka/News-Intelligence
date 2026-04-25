"""
Simple topic classification via keyword matching.
Production: Replace with ML model (e.g., zero-shot classification).
"""
from typing import Optional

TOPIC_KEYWORDS = {
    "politics": ["election", "government", "parliament", "congress", "senate", "president", "minister", "policy", "vote", "campaign", "political"],
    "business": ["stock", "market", "economy", "trade", "business", "company", "corporate", "finance", "investment", "revenue", "profit"],
    "technology": ["tech", "software", "ai", "artificial intelligence", "crypto", "blockchain", "startup", "innovation", "digital", "cyber"],
    "health": ["health", "medical", "doctor", "hospital", "disease", "vaccine", "covid", "pandemic", "patient", "treatment", "medicine"],
    "science": ["research", "study", "scientist", "discovery", "experiment", "climate", "space", "nasa", "physics", "biology"],
    "sports": ["sport", "game", "team", "player", "match", "league", "championship", "football", "basketball", "soccer", "olympic"],
    "entertainment": ["movie", "film", "music", "actor", "celebrity", "artist", "album", "concert", "award", "netflix", "show"],
    "world": ["war", "conflict", "international", "global", "country", "nation", "diplomacy", "foreign", "united nations", "crisis"],
    "environment": ["climate", "environment", "pollution", "renewable", "carbon", "emission", "sustainable", "wildlife", "conservation"],
}


def classify_topic(text: str) -> Optional[str]:
    """
    Returns the best-matching topic, or None if no strong match.
    text: article title + description concatenated
    """
    if not text:
        return None
    
    text_lower = text.lower()
    scores = {}
    
    for topic, keywords in TOPIC_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > 0:
            scores[topic] = count
    
    if not scores:
        return "general"
    
    # Return topic with highest keyword density
    return max(scores.items(), key=lambda x: x[1])[0]


def classify_topic_multi(text: str, threshold: int = 2) -> list[str]:
    """
    Returns multiple topics if article spans categories.
    threshold: minimum keyword matches to include topic
    """
    if not text:
        return []
    
    text_lower = text.lower()
    matched = []
    
    for topic, keywords in TOPIC_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count >= threshold:
            matched.append(topic)
    
    return matched if matched else ["general"]
