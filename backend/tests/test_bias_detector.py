"""
Tests for upgraded multi-signal bias detector.
Run: pytest backend/tests/test_bias_detector.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
import json
from backend.services.bias_detector import (
    detect_bias,
    detect_bias_full,
    aggregate_topic_bias,
    BiasResult,
    SOURCE_BASELINE,
    _score_to_label,
    _keyword_signal,
    _framing_signal,
)


# ── Label mapping ─────────────────────────────────────────────────────────────

class TestLabelMapping:
    def test_left(self):          assert _score_to_label(-0.5) == "left"
    def test_center_left(self):   assert _score_to_label(-0.25) == "center-left"
    def test_center(self):        assert _score_to_label(0.0) == "center"
    def test_center_right(self):  assert _score_to_label(0.25) == "center-right"
    def test_right(self):         assert _score_to_label(0.6) == "right"
    def test_boundary_left(self): assert _score_to_label(-0.40) == "center-left"
    def test_boundary_right(self):assert _score_to_label(0.40) == "center-right"


# ── Source baseline ───────────────────────────────────────────────────────────

class TestSourceBaseline:
    def test_reuters_neutral(self):
        r = detect_bias_full("The government announced new regulations today.", "Reuters")
        assert abs(r.score) < 0.15
        assert r.source_baseline == 0.0

    def test_guardian_left_lean(self):
        r = detect_bias_full("New policy announced today.", "The Guardian")
        assert r.score < 0
        assert r.source_baseline == -0.30

    def test_unknown_source_no_baseline(self):
        r = detect_bias_full("Some article text here.", "Unknown Outlet")
        assert r.source_baseline is None

    def test_source_signal_in_output(self):
        r = detect_bias_full("Text", "Reuters")
        assert any("Reuters" in s for s in r.signals)

    def test_all_registry_sources_valid_range(self):
        for source, baseline in SOURCE_BASELINE.items():
            assert -1.0 <= baseline <= 1.0, f"{source} has out-of-range baseline {baseline}"


# ── Keyword signal ────────────────────────────────────────────────────────────

class TestKeywordSignal:
    def test_left_keywords_score_negative(self):
        score, sigs = _keyword_signal("progressive climate crisis social justice marginalized")
        assert score < -0.20

    def test_right_keywords_score_positive(self):
        score, sigs = _keyword_signal("conservative traditional values free market tax cuts")
        assert score > 0.20

    def test_loaded_left_high_magnitude(self):
        score, sigs = _keyword_signal("fascist radical left voter suppression")
        assert score < -0.50

    def test_loaded_right_high_magnitude(self):
        score, sigs = _keyword_signal("radical left fake news deep state")
        assert score > 0.50

    def test_neutral_text_no_signal(self):
        score, sigs = _keyword_signal("the committee met on wednesday morning")
        assert score == 0.0
        assert sigs == []

    def test_signals_list_contains_phrases(self):
        score, sigs = _keyword_signal("free market conservative tax cuts")
        assert len(sigs) > 0
        assert any("keyword" in s.lower() for s in sigs)

    def test_mixed_keywords_average(self):
        # One strong left + one strong right should average near center
        score, _ = _keyword_signal("progressive free market")
        assert abs(score) < 0.15


# ── Framing signal ────────────────────────────────────────────────────────────

class TestFramingSignal:
    def test_attack_framing_detected(self):
        intensity, sigs = _framing_signal("the legislation threatens to destroy democratic values")
        assert intensity > 0

    def test_solution_framing_dampens(self):
        # Pure balanced language should produce low intensity
        intensity, sigs = _framing_signal("according to data research shows evidence suggests")
        assert intensity < 0.1

    def test_combined_reduces_intensity(self):
        attack_only, _ = _framing_signal("attack on threatens destroys")
        combined, _ = _framing_signal("attack on threatens according to data both sides in contrast")
        assert combined <= attack_only

    def test_clean_text_zero_intensity(self):
        intensity, _ = _framing_signal("the president visited france on tuesday")
        assert intensity == 0.0


# ── Full bias detection ───────────────────────────────────────────────────────

class TestDetectBiasFull:
    def test_returns_bias_result_type(self):
        r = detect_bias_full("some text")
        assert isinstance(r, BiasResult)

    def test_empty_text_returns_center(self):
        r = detect_bias_full("")
        assert r.score == 0.0
        assert r.label == "center"
        assert r.confidence == 0.0

    def test_whitespace_only_returns_center(self):
        r = detect_bias_full("   \n\t  ")
        assert r.score == 0.0

    def test_score_range_clamped(self):
        r = detect_bias_full("radical left fake news deep state socialist agenda cancel culture woke")
        assert -1.0 <= r.score <= 1.0

    def test_confidence_range(self):
        r = detect_bias_full("text", "Reuters")
        assert 0.0 <= r.confidence <= 1.0

    def test_high_confidence_with_strong_evidence(self):
        r = detect_bias_full(
            "progressive climate crisis social justice voter suppression",
            source_name="The Guardian"
        )
        assert r.confidence > 0.50

    def test_low_confidence_no_evidence(self):
        r = detect_bias_full("the meeting was held on thursday")
        assert r.confidence < 0.25

    def test_signals_are_list(self):
        r = detect_bias_full("some text", "Reuters")
        assert isinstance(r.signals, list)
        assert len(r.signals) > 0

    def test_to_dict_serializable(self):
        r = detect_bias_full("progressive climate crisis", "The Guardian")
        d = r.to_dict()
        # Must be JSON-serializable
        json.dumps(d)
        assert "score" in d
        assert "signals" in d

    def test_known_right_source_plus_right_keywords(self):
        r = detect_bias_full(
            "conservative values free market tax cuts deregulation",
            source_name="Arab News"  # 0.20 baseline
        )
        assert r.score > 0.0
        assert r.label in ("center-right", "right")

    def test_left_source_amplifies_left_keywords(self):
        r = detect_bias_full(
            "progressive climate crisis social justice inequality",
            source_name="The Guardian"  # -0.30 baseline
        )
        r2 = detect_bias_full(
            "progressive climate crisis social justice inequality",
            source_name="Reuters"  # 0.00 baseline
        )
        assert r.score < r2.score  # Guardian version should be more negative


# ── Backward compatibility ────────────────────────────────────────────────────

class TestBackwardCompat:
    def test_detect_bias_returns_tuple(self):
        result = detect_bias("conservative free market", "Reuters")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_detect_bias_score_is_float(self):
        score, label = detect_bias("some text")
        assert isinstance(score, float)

    def test_detect_bias_label_is_string(self):
        score, label = detect_bias("progressive climate crisis")
        assert isinstance(label, str)
        assert label in ("left", "center-left", "center", "center-right", "right")


# ── Aggregate bias ────────────────────────────────────────────────────────────

class TestAggregateTopicBias:
    def test_empty_returns_defaults(self):
        r = aggregate_topic_bias([])
        assert r["count"] == 0
        assert r["mean"] == 0.0

    def test_mean_calculation(self):
        r = aggregate_topic_bias([-0.3, -0.1, 0.0, 0.1, 0.3])
        assert r["mean"] == 0.0
        assert r["count"] == 5

    def test_contested_flag_high_spread(self):
        r = aggregate_topic_bias([-0.7, 0.0, 0.7])
        assert r["contested"] == True
        assert r["spread"] >= 0.6

    def test_not_contested_low_spread(self):
        r = aggregate_topic_bias([-0.1, 0.0, 0.05])
        assert r["contested"] == False

    def test_distribution_counts_correct(self):
        scores = [-0.8, -0.25, 0.0, 0.25, 0.8]
        r = aggregate_topic_bias(scores)
        dist = r["distribution"]
        assert dist["left"] == 1
        assert dist["center_left"] == 1
        assert dist["center"] == 1
        assert dist["center_right"] == 1
        assert dist["right"] == 1

    def test_single_score(self):
        r = aggregate_topic_bias([0.5])
        assert r["mean"] == 0.5
        assert r["label"] == "right"
        assert r["count"] == 1


# ── Integration: processing pipeline compatibility ────────────────────────────

class TestProcessingIntegration:
    def test_full_pipeline_fields_present(self):
        """Simulate what processing.py does with the new result."""
        r = detect_bias_full(
            "Progressive policies on climate crisis and social justice",
            source_name="The Guardian"
        )
        # Fields that get stored in DB
        assert isinstance(r.score, float)
        assert isinstance(r.label, str)
        assert isinstance(r.confidence, float)
        assert isinstance(r.signals, list)
        # JSON serialization works (for bias_signals_json column)
        json_str = json.dumps(r.signals)
        restored = json.loads(json_str)
        assert restored == r.signals

    def test_source_none_does_not_crash(self):
        """No source_name should not raise any exception."""
        r = detect_bias_full("Some article about politics")
        assert r is not None
        assert r.source_baseline is None
