"""
Multi-signal bias detection engine.

Three independent signals combined into a single explainable score:
  1. Source baseline  - known outlet lean from curated registry (weight 0.35)
  2. Keyword framing  - expanded phrase matching with per-phrase weights (weight 0.45)
  3. Framing tone     - attack/solution language amplifies directional lean (weight 0.20)

Score: -1.0 (far left) to +1.0 (far right), 0.0 = center
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Source Baseline Registry
# Compiled from AllSides, Media Bias/Fact Check, Ad Fontes Media.
# Omit ambiguous sources - only well-documented outlets included.
# ---------------------------------------------------------------------------
SOURCE_BASELINE: dict = {
    "Reuters":           0.00,
    "Reuters Top News":  0.00,
    "Reuters Business":  0.00,
    "Reuters World":     0.00,
    "AP News":           0.00,
    "Associated Press":  0.00,
    "BBC World":        -0.05,
    "BBC Business":     -0.05,
    "The Guardian":     -0.30,
    "The Guardian US":  -0.30,
    "The Guardian World":-0.30,
    "The Economist":    -0.05,
    "Deutsche Welle":   -0.05,
    "DW Europe":        -0.05,
    "France 24":        -0.05,
    "Euronews":          0.00,
    "POLITICO Europe":  -0.05,
    "EUobserver":       -0.10,
    "Al Jazeera":       -0.15,
    "Al Arabiya":        0.15,
    "Arab News":         0.20,
    "Jerusalem Post":    0.25,
    "Middle East Eye":  -0.20,
    "NPR News":         -0.20,
    "The Hindu":        -0.15,
    "The Wire":         -0.25,
    "NDTV":             -0.05,
    "Times of India":    0.00,
    "The Print":        -0.10,
    "SCMP":              0.05,
    "SCMP China":        0.05,
    "Channel News Asia": 0.00,
    "Nikkei Asia":       0.05,
    "Japan Times":       0.00,
    "Korea Herald":      0.00,
    "The Diplomat":      0.00,
}

SOURCE_WEIGHT = 0.35

# ---------------------------------------------------------------------------
# Keyword Signal
# (phrase, score_contribution) where negative=left, positive=right
# Absolute value > 0.5 = loaded/strong framing
# ---------------------------------------------------------------------------
KEYWORD_SIGNALS: list = [
    # Left indicators
    ("progressive",           -0.40),
    ("climate crisis",        -0.50),
    ("social justice",        -0.45),
    ("systemic racism",       -0.55),
    ("structural inequality", -0.50),
    ("marginalized",          -0.35),
    ("equity",                -0.30),
    ("reproductive rights",   -0.45),
    ("living wage",           -0.40),
    ("universal healthcare",  -0.45),
    ("wealth inequality",     -0.40),
    ("corporate greed",       -0.55),
    ("working class",         -0.35),
    ("gun control",           -0.40),
    ("climate justice",       -0.50),
    ("voter suppression",     -0.55),
    ("income inequality",     -0.40),
    ("defund the police",     -0.55),
    ("police brutality",      -0.45),
    ("reparations",           -0.40),
    # Left loaded
    ("fascist",               -0.70),
    ("white supremacist",     -0.65),
    ("war on women",          -0.65),
    ("climate denier",        -0.60),
    ("assault weapons",       -0.50),
    # Right indicators
    ("conservative",           0.35),
    ("traditional values",     0.40),
    ("fiscal responsibility",  0.40),
    ("free market",            0.35),
    ("personal freedom",       0.40),
    ("second amendment",       0.45),
    ("border security",        0.45),
    ("law and order",          0.40),
    ("tax cuts",               0.40),
    ("deregulation",           0.40),
    ("religious liberty",      0.45),
    ("pro-life",               0.50),
    ("school choice",          0.40),
    ("limited government",     0.45),
    ("parental rights",        0.40),
    ("election integrity",     0.45),
    ("energy independence",    0.35),
    ("big government",         0.45),
    ("government overreach",   0.45),
    # Right loaded
    ("radical left",           0.70),
    ("socialist agenda",       0.65),
    ("liberal elite",          0.65),
    ("fake news",              0.65),
    ("cancel culture",         0.55),
    ("woke",                   0.55),
    ("deep state",             0.65),
    ("illegal alien",          0.55),
    ("open borders",           0.55),
    ("globalist",              0.55),
]

KEYWORD_WEIGHT = 0.45

# ---------------------------------------------------------------------------
# Framing Tone
# Attack framing amplifies directional lean; balanced language dampens it
# ---------------------------------------------------------------------------
ATTACK_FRAMING: list = [
    "attack on", "assault on", "threatens", "destroys", "dismantles",
    "crackdown", "targets", "weaponizes", "exploits", "manipulates",
    "propaganda", "indoctrination", "radical", "extremist",
]

SOLUTION_FRAMING: list = [
    "according to data", "research shows", "evidence suggests",
    "experts say", "both sides", "on the other hand", "in contrast",
    "however", "nevertheless", "officials noted", "the report found",
    "according to", "data indicates", "studies show",
]

FRAMING_WEIGHT = 0.20


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class BiasResult:
    score: float                     # -1.0 to +1.0
    label: str                       # left|center-left|center|center-right|right
    confidence: float                # 0.0 to 1.0
    source_baseline: Optional[float] # None if source unknown
    keyword_score: float
    framing_intensity: float
    signals: list                    # human-readable explanation

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _score_to_label(score: float) -> str:
    if score < -0.40:  return "left"
    if score < -0.15:  return "center-left"
    if score >  0.40:  return "right"
    if score >  0.15:  return "center-right"
    return "center"


def _keyword_signal(text_lower: str):
    hits = [(phrase, val) for phrase, val in KEYWORD_SIGNALS if phrase in text_lower]
    if not hits:
        return 0.0, []
    raw = sum(v for _, v in hits) / len(hits)
    top5 = sorted(hits, key=lambda x: abs(x[1]), reverse=True)[:5]
    sigs = [
        ("Left" if v < 0 else "Right") + " keyword: '" + p + "' (" + ("+%.2f" % v if v >= 0 else "%.2f" % v) + ")"
        for p, v in top5
    ]
    return max(-1.0, min(1.0, raw)), sigs


def _framing_signal(text_lower: str):
    n_attack = sum(1 for p in ATTACK_FRAMING if p in text_lower)
    n_solution = sum(1 for p in SOLUTION_FRAMING if p in text_lower)
    intensity = max(0.0, min(1.0, n_attack / max(len(ATTACK_FRAMING) * 0.3, 1) - n_solution * 0.05))
    sigs = []
    if n_attack:
        examples = [p for p in ATTACK_FRAMING if p in text_lower][:3]
        sigs.append("Attack framing (" + str(n_attack) + "): " + ", ".join(examples))
    if n_solution:
        sigs.append("Balanced framing (" + str(n_solution) + " markers) dampens score")
    return round(intensity, 3), sigs


def _confidence(source_known: bool, keyword_hits: int, framing_intensity: float, score: float) -> float:
    base = 0.0
    if source_known:       base += 0.30
    if keyword_hits > 0:   base += min(0.40, keyword_hits * 0.08)
    if framing_intensity > 0.2: base += 0.15
    base += abs(score) * 0.15
    return round(min(1.0, base), 2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def detect_bias_full(text: str, source_name: Optional[str] = None) -> BiasResult:
    """
    Full multi-signal bias analysis with explainability.

    Args:
        text: concatenated title + description + content
        source_name: publisher name for baseline lookup

    Returns:
        BiasResult with score, label, confidence, and signal breakdown
    """
    if not text or not text.strip():
        return BiasResult(
            score=0.0, label="center", confidence=0.0,
            source_baseline=None, keyword_score=0.0,
            framing_intensity=0.0, signals=["No text provided"]
        )

    text_lower = text.lower()
    signals = []

    # Signal 1: source baseline
    baseline = SOURCE_BASELINE.get(source_name, None) if source_name else None
    if baseline is not None:
        signals.append("Source '" + str(source_name) + "' baseline: " + ("%+.2f" % baseline))
    elif source_name:
        signals.append("Source '" + str(source_name) + "' not in registry")
    source_contrib = (baseline if baseline is not None else 0.0) * SOURCE_WEIGHT

    # Signal 2: keyword framing
    kw_score, kw_sigs = _keyword_signal(text_lower)
    signals.extend(kw_sigs)
    keyword_hits = sum(1 for p, _ in KEYWORD_SIGNALS if p in text_lower)
    keyword_contrib = kw_score * KEYWORD_WEIGHT

    # Signal 3: framing tone (amplifier, not additive direction)
    framing_intensity, framing_sigs = _framing_signal(text_lower)
    signals.extend(framing_sigs)
    directional = source_contrib + keyword_contrib
    framing_amp = directional * framing_intensity * FRAMING_WEIGHT

    raw = source_contrib + keyword_contrib + framing_amp
    final_score = round(max(-1.0, min(1.0, raw)), 3)
    label = _score_to_label(final_score)
    conf = _confidence(baseline is not None, keyword_hits, framing_intensity, final_score)

    if not signals:
        signals.append("No strong indicators — scored as center")

    return BiasResult(
        score=final_score,
        label=label,
        confidence=conf,
        source_baseline=baseline,
        keyword_score=round(kw_score, 3),
        framing_intensity=framing_intensity,
        signals=signals,
    )


def detect_bias(text: str, source_name: Optional[str] = None) -> Tuple[float, str]:
    """Backward-compatible: returns (score, label)."""
    r = detect_bias_full(text, source_name)
    return r.score, r.label


def aggregate_topic_bias(scores: list) -> dict:
    """Aggregate bias scores across multiple articles for a topic."""
    if not scores:
        return {"mean": 0.0, "label": "center", "contested": False, "spread": 0.0, "count": 0}
    mean = round(sum(scores) / len(scores), 3)
    spread = round(max(scores) - min(scores), 3)
    return {
        "mean": mean,
        "label": _score_to_label(mean),
        "contested": spread > 0.6,
        "spread": spread,
        "count": len(scores),
        "distribution": {
            "left":         sum(1 for s in scores if s < -0.40),
            "center_left":  sum(1 for s in scores if -0.40 <= s < -0.15),
            "center":       sum(1 for s in scores if -0.15 <= s <= 0.15),
            "center_right": sum(1 for s in scores if 0.15 < s <= 0.40),
            "right":        sum(1 for s in scores if s > 0.40),
        }
    }


def get_bias_confidence(score: float) -> float:
    """Backward-compatible helper."""
    return min(1.0, abs(score) * 1.5)
