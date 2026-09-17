"""Tests for the open-montage assembler's pure helpers."""

import pytest

from scripts.om_assemble import GRADES, trim_window


def test_trim_window_prefers_the_explicit_keep_range():
    assert trim_window({"id": "shot_01", "trim": {"keep_seconds": "0.0-3.0"}, "seconds_delivered": 4}) == (0.0, 3.0)
    assert trim_window({"id": "shot_03", "trim": {"keep_seconds": "0.5-2.5"}}) == (0.5, 2.0)


def test_trim_window_falls_back_to_delivered_seconds_then_the_whole_clip():
    assert trim_window({"id": "shot_02", "seconds_delivered": 6}) == (0.0, 6.0)
    assert trim_window({"id": "shot_02"}) == (0.0, None)


def test_trim_window_refuses_a_range_that_keeps_nothing():
    with pytest.raises(ValueError, match="keeps nothing"):
        trim_window({"id": "shot_01", "trim": {"keep_seconds": "3.0-3.0"}})


def test_the_named_grade_exists_and_none_is_a_noop():
    assert GRADES["none"] is None
    assert "colorbalance" in GRADES["amber-blue-night"]


import pytest

from scripts.om_assemble import EditError, cut_report, edit_plan, finish_filter, join_filter

CONTRACT = {"shots": [{"id": "shot_01", "seconds": 4, "trim": {"keep_seconds": "0.0-3.0"}},
                      {"id": "shot_02", "seconds": 6}, {"id": "shot_03", "seconds": 4}]}


def test_without_instructions_the_cut_is_the_contract():
    plan = edit_plan(CONTRACT, {})
    assert [e["shot"] for e in plan] == ["shot_01", "shot_02", "shot_03"]
    assert {e["transition"] for e in plan} == {"cut"}


def test_instructions_reorder_drop_retrim_and_dissolve():
    plan = edit_plan(CONTRACT, {"sequence": [
        {"shot": "shot_03", "transition": "dissolve"},  # first shot: nothing to dissolve from
        {"shot": "shot_01", "keep_seconds": "1.0-2.5", "transition": {"type": "dissolve", "seconds": 0.4}}]})
    assert [e["shot"] for e in plan] == ["shot_03", "shot_01"]
    assert plan[0]["transition"] == "cut"
    assert plan[1]["transition_seconds"] == 0.4 and trim_window(plan[1]["row"]) == (1.0, 1.5)
    assert CONTRACT["shots"][0]["trim"] == {"keep_seconds": "0.0-3.0"}  # the contract is not mutated


def test_a_wrong_shot_or_transition_is_explained():
    with pytest.raises(EditError, match="shot_09.*the contract has: shot_01"):
        edit_plan(CONTRACT, {"sequence": [{"shot": "shot_09"}]})
    with pytest.raises(EditError, match="wipe"):
        edit_plan(CONTRACT, {"sequence": [{"shot": "shot_01"}, {"shot": "shot_02", "transition": "wipe"}]})


def test_join_filter_mixes_cuts_and_dissolves_and_knows_the_runtime():
    plan = edit_plan(CONTRACT, {"sequence": [{"shot": "shot_01"}, {"shot": "shot_02"},
                                             {"shot": "shot_03", "transition": {"type": "dissolve", "seconds": 0.5}}]})
    chains, runtime = join_filter([3.0, 6.0, 4.0], plan, with_audio=True, fps=24)
    graph = ";".join(chains)
    assert "[v0][v1]concat=n=2:v=1:a=0[vj1]" in graph
    assert "[vj1][v2]xfade=transition=fade:duration=0.5:offset=8.500[vjoin]" in graph
    # a cut keeps the sound as long as the picture: edge fades + concat, never a crossfade
    assert "[0:a]afade=t=out:st=2.940:d=0.06[a0]" in graph and "[a0][a1]concat=n=2:v=0:a=1[aj1]" in graph
    assert "[1:a]afade=t=in:st=0:d=0.06[a1]" in graph  # shot_02 is dissolved out of, so no fade-out
    assert "[aj1][a2]acrossfade=d=0.5" in graph and graph.endswith("[ajoin]")
    assert runtime == 12.5
    with pytest.raises(EditError, match="as long as a shot"):
        join_filter([3.0, 0.4], edit_plan(CONTRACT, {"sequence": [
            {"shot": "shot_01"}, {"shot": "shot_02", "transition": "dissolve"}]}), False, 24)


def test_finish_filter_titles_music_and_fades(tmp_path):
    edit = {"titles": [{"text": "It's 3 a.m.: \"awake\"", "start": 0.5, "end": 2.5, "position": "top"}],
            "music": {"file": "m.mp3", "volume_db": -20, "fade_out_seconds": 2}, "fade_out_seconds": 1}
    chains, has_sound = finish_filter(edit, 12.5, True, 3, [tmp_path / "t.txt"])
    graph = ";".join(chains)
    assert has_sound and "drawtext=textfile=" in graph and "It's" not in graph  # text travels by file
    assert "[3:a]atrim=0:12.500" in graph and "volume=-20dB" in graph and "amix=inputs=2" in graph
    assert "fade=t=out:st=11.500:d=1" in graph
    muted, has_sound = finish_filter({"clip_audio": "mute"}, 5.0, True, None, [])
    assert not has_sound and muted == ["[vjoin]null[v]"]
    with pytest.raises(EditError, match="the cut is 5.00 s"):
        finish_filter({"titles": [{"text": "x", "start": 4, "end": 9}]}, 5.0, False, None, [tmp_path / "t.txt"])


def test_cut_report_places_each_cut_and_accounts_for_a_dissolve():
    plan = edit_plan(CONTRACT, {"sequence": [{"shot": "shot_01"}, {"shot": "shot_02"},
                                             {"shot": "shot_03", "transition": {"type": "dissolve", "seconds": 0.5}}]})
    report = cut_report(plan, [3.0, 6.0, 4.0], 12.5, 12.5, "none")
    assert report["cuts_at"] == [3.0, 8.5] and report["shots"][-1]["ends_at"] == 12.5
    assert report["runtime_ok"] is True
    assert cut_report(plan[:1], [4.0], 4.0, 3, "none")["runtime_ok"] is False
    assert "runtime_ok" not in cut_report(plan[:1], [4.0], 4.0, None, "none")
