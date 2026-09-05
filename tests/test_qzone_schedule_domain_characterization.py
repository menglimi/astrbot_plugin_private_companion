from __future__ import annotations

import random
from unittest.mock import patch

from astrbot_plugin_private_companion.qzone_integration import QzoneMixin


def test_window_parser_preserves_boundary_and_separator_semantics() -> None:
    raw = "00:00-06:00, 05:30-07:00\n23:00-24:00\n24:00-24:00\n12:60-13:00"
    assert QzoneMixin._qzone_parse_windows(raw) == [(0, 420), (1380, 1440)]


def test_window_parser_splits_cross_midnight_then_merges_edges() -> None:
    assert QzoneMixin._qzone_parse_windows("22:00-02:00\n02:00-03:00") == [(0, 180), (1320, 1440)]


def test_subtract_ranges_preserves_ordered_open_remainders() -> None:
    assert QzoneMixin._qzone_subtract_ranges((0, 1440), ((0, 360), (1380, 1440), (720, 780))) == [
        (360, 720), (780, 1380)
    ]


def test_hhmm_parser_preserves_extended_agenda_hours() -> None:
    assert QzoneMixin._qzone_hhmm_to_minutes(" 47:59 ") == 2879
    assert QzoneMixin._qzone_hhmm_to_minutes("48:00") is None
    assert QzoneMixin._qzone_hhmm_to_minutes("07:5") is None


def test_text_length_ignores_whitespace_and_keeps_profile_tolerance() -> None:
    assert QzoneMixin._qzone_text_length_ok("a " * 12, "short")
    assert not QzoneMixin._qzone_text_length_ok("a" * 11, "short")


def test_ngram_overlap_preserves_short_and_repeated_text_semantics() -> None:
    assert QzoneMixin._qzone_ngram_shared_count("same", "same", n=8) == 1
    assert QzoneMixin._qzone_ngram_shared_count("same", "different", n=8) == 0
    assert QzoneMixin._qzone_ngram_shared_count("aaaa", "aaaa") == 1


def test_length_profile_sequence_preserves_shape_under_seeded_rng() -> None:
    with patch.object(random, "random", return_value=0.0), patch.object(
        random, "randrange", return_value=1
    ), patch.object(random, "shuffle", side_effect=lambda values: None):
        assert QzoneMixin._qzone_length_profile_sequence(4) == ["short", "long", "short", "medium"]
