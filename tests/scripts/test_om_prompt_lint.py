"""Tests for the open-montage prompt lint and the frame calculator."""

import pytest

from scripts.om_prompt_lint import (
    distance_for_height,
    frame_size,
    lint_cross_shot,
    lint_shot,
    sections,
)

GOOD = """GLOBAL STYLE
Night interior, 16:9.

REFERENCE USE
The reference images show this room empty. In this shot he is in the bed from the first frame.

SCENE
A man sleeps.

CHARACTERS
One man, alone, early thirties.

CARRY-OVER
Identical to the previous shot: the same pillows.

LOCATION
A small bedroom.

FIRST FRAME AND BLOCKING
He lies on his back.

Shot 1: One continuous 4-second shot on a 47 degree diagonal field of view lens.
0.0 to 1.4 s — he turns his head to his left.
1.4 to 3.0 s — he turns his head to his right.
3.0 to 4.0 s — he holds still.

OPTICS / CAMERA
47 degrees diagonal field of view. LENS LOCK: one focal length, no zoom.

LIGHTING
Two sources only.

AUDIO
Room tone. No music, no dialogue.

IDENTITY / NO-IP LOCK
He is a wholly original, invented person.

POSITIVE LOCKS
His eyes stay closed. The pillows stay against the wall.
"""
ROW = {"id": "shot_01", "seconds_generated": 4, "lens": "47 degrees diagonal field of view",
       "reference_ids": ["assets/reference/room_plate.png"], "trim": {"keep_seconds": "0.0-3.0"}}


def codes(findings, level=None):
    return [f.code for f in findings if level is None or f.level == level]


def test_frame_size_matches_the_numbers_the_reviews_were_spent_on():
    width, height = frame_size(63, 2.0)
    assert (round(width, 2), round(height, 2)) == (2.14, 1.20)  # a bed does not fit under a ceiling
    assert round(frame_size(47, 1.0)[0], 2) == 0.76               # a window does not fit at one metre
    assert round(distance_for_height(47, 1.0), 2) == 2.35         # shot_02's settled frame


def test_sections_treats_shot_lines_as_body():
    parts = sections(GOOD)
    assert "Shot 1: One continuous" in parts["FIRST FRAME AND BLOCKING"]
    assert list(parts)[:3] == ["GLOBAL STYLE", "REFERENCE USE", "SCENE"]


def test_a_sound_prompt_has_no_errors():
    assert codes(lint_shot("shot_01", GOOD, ROW), "error") == []


def test_a_beat_wholly_inside_the_trimmed_tail_is_flagged():
    # the defect that would have cut shot_01's brief-required shoulder shift
    findings = lint_shot("shot_01", GOOD, ROW)
    assert "beat-in-trimmed-tail" in codes(findings, "warning")


def test_person_shot_without_identity_lock_is_an_error():
    prompt = GOOD.replace("IDENTITY / NO-IP LOCK\nHe is a wholly original, invented person.\n\n", "")
    assert "no-identity-lock" in codes(lint_shot("shot_01", prompt, ROW), "error")


def test_person_shot_with_references_needs_reference_use():
    start, end = GOOD.index("REFERENCE USE"), GOOD.index("SCENE")
    prompt = GOOD[:start] + GOOD[end:]
    assert "no-reference-use" in codes(lint_shot("shot_01", prompt, ROW), "error")
    no_refs = {**ROW, "reference_ids": []}
    assert "no-reference-use" not in codes(lint_shot("shot_01", prompt, no_refs))


def test_an_empty_room_clip_needs_neither_person_lock():
    prompt = GOOD.replace("One man, alone, early thirties.", "Nobody. The bed is empty.")
    prompt = prompt.replace("IDENTITY / NO-IP LOCK\nHe is a wholly original, invented person.\n\n", "")
    assert "no-identity-lock" not in codes(lint_shot("ref_room", prompt, ROW))


def test_lens_in_the_prompt_must_be_the_contract_lens():
    row = {**ROW, "lens": "63 degrees diagonal field of view (35 mm equivalent)"}
    assert "lens-mismatch" in codes(lint_shot("shot_01", GOOD, row), "error")


def test_beats_may_not_overrun_the_clip():
    prompt = GOOD.replace("3.0 to 4.0 s", "3.0 to 5.5 s")
    assert "beats-overrun" in codes(lint_shot("shot_01", prompt, ROW), "error")


def test_exclusive_positive_lock_is_flagged():
    # verbatim shape of the shot_02 lock that deleted the sweater, the blanket and the lamp
    prompt = GOOD.replace("His eyes stay closed.",
                          "The frame holds his face, the pillows and the wall, and those three things only.")
    assert "exclusive-lock" in codes(lint_shot("shot_01", prompt, ROW), "warning")


@pytest.mark.parametrize("phrase", ["as though entering", "like a bowl of blue"])
def test_comparisons_are_flagged(phrase):
    prompt = GOOD.replace("A man sleeps.", f"The camera moves {phrase}.")
    assert "figurative" in codes(lint_shot("shot_01", prompt, ROW), "warning")


def test_carry_over_must_be_byte_identical_across_shots():
    drifted = GOOD.replace("the same pillows", "the same four pillows")
    assert codes(lint_cross_shot({"shot_02": GOOD, "shot_03": GOOD})) == []
    assert "carry-over-differs" in codes(lint_cross_shot({"shot_02": GOOD, "shot_03": drifted}), "error")


def test_a_shot_may_extend_the_shared_character_text_but_not_change_it():
    extended = GOOD.replace("early thirties.", "early thirties. His feet are bare.")
    changed = GOOD.replace("early thirties", "mid-twenties")
    assert "characters-differ" not in codes(lint_cross_shot({"a": GOOD, "b": extended}))
    assert "characters-differ" in codes(lint_cross_shot({"a": GOOD, "b": changed}), "warning")
