"""Tests for the open-montage shot runner's pure helpers. No network, no spend."""

import json

import pytest

from scripts.om_shot import (
    INPUT_PERSON_FILTER,
    OTHER_FAILURE,
    OUTPUT_LIKENESS_FILTER,
    ContractError,
    actual_usd,
    append_spend,
    build_inputs,
    classify_error,
    next_attempt_path,
    split_references,
)

# Verbatim from the restless-night run, 2026-09-17.
PERSON_REFUSAL = (
    "Ark Seedance request failed: 400 Client Error: Bad Request for url: "
    "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks; "
    "InputImageSensitiveContentDetected.PrivacyInformation: The request failed because "
    "the input image 'content[1]' 'content[2]' may contain real person."
)
LIKENESS_REFUSAL = (
    "Ark Seedance task failed: OutputVideoSensitiveContentDetected.PolicyViolation: The "
    "request failed because the output video may be related to copyright restrictions."
)


def test_classify_error_tells_the_two_filters_apart():
    assert classify_error(PERSON_REFUSAL) == INPUT_PERSON_FILTER
    assert classify_error(LIKENESS_REFUSAL) == OUTPUT_LIKENESS_FILTER
    assert classify_error("401 Unauthorized") == OTHER_FAILURE
    assert classify_error(None) == OTHER_FAILURE


def test_split_references_routes_assets_urls_and_files(tmp_path):
    plate = tmp_path / "assets" / "reference" / "room_plate.png"
    plate.parent.mkdir(parents=True)
    plate.write_bytes(b"png")
    paths, urls = split_references(
        ["asset://abc123", "https://ark.example/last_frame.png?sig=1", "assets/reference/room_plate.png"],
        tmp_path,
    )
    assert paths == [str(plate)]
    assert urls == ["asset://abc123", "https://ark.example/last_frame.png?sig=1"]


def test_split_references_refuses_pending_and_missing(tmp_path):
    with pytest.raises(ContractError, match="pending"):
        split_references(["PENDING:seated_plate.png (from accepted shot_02)"], tmp_path)
    with pytest.raises(ContractError, match="not found"):
        split_references(["assets/reference/nope.png"], tmp_path)


def _project(tmp_path, row, route=None):
    prompts = tmp_path / "artifacts" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / f"{row['id']}.txt").write_text("GLOBAL STYLE\nnight interior\n")
    contract = {"shots": [row]}
    if route:
        contract["route"] = route
    return contract


def test_build_inputs_uses_team_defaults_and_generated_seconds(tmp_path):
    contract = _project(tmp_path, {"id": "shot_01", "seconds_generated": 4, "seconds_delivered": 3})
    inputs = build_inputs(contract, "shot_01", tmp_path)
    assert inputs["duration"] == 4
    assert inputs["resolution"] == "480p"
    assert inputs["model_variant"] == "2.5"
    assert inputs["custom_price_cny_per_million_tokens"] == 77.0
    assert inputs["operation"] == "text_to_video"  # no references
    assert inputs["output_path"].endswith("assets/video/shot_01.mp4")
    assert "reference_image_paths" not in inputs


def test_build_inputs_rejects_durations_the_provider_refuses(tmp_path):
    contract = _project(tmp_path, {"id": "shot_03", "seconds": 2})
    with pytest.raises(ContractError, match="4 or more"):
        build_inputs(contract, "shot_03", tmp_path)


def test_build_inputs_passes_last_frame_and_route_overrides(tmp_path):
    contract = _project(
        tmp_path,
        {"id": "face_sheet", "seconds": 4, "return_last_frame": True, "reference_ids": ["asset://char-7"]},
        route={"resolution": "720p"},
    )
    inputs = build_inputs(contract, "face_sheet", tmp_path)
    assert inputs["return_last_frame"] is True
    assert inputs["resolution"] == "720p"
    assert inputs["operation"] == "reference_to_video"
    assert inputs["reference_image_urls"] == ["asset://char-7"]


def test_build_inputs_sends_the_previous_take_back_for_an_edit(tmp_path):
    from scripts.om_shot import build_inputs
    contract = _project(tmp_path, {"id": "shot_02", "seconds": 6,
                                   "reference_video_urls": ["https://ark.example/v.mp4"]})
    inputs = build_inputs(contract, "shot_02", tmp_path)
    assert inputs["reference_video_urls"] == ["https://ark.example/v.mp4"]
    assert inputs["operation"] == "reference_to_video"


def test_build_inputs_names_the_known_shots_when_the_id_is_wrong(tmp_path):
    contract = _project(tmp_path, {"id": "shot_01", "seconds": 4})
    with pytest.raises(ContractError, match="shot_01"):
        build_inputs(contract, "shot_09", tmp_path)


def test_next_attempt_path_never_reuses_a_name(tmp_path):
    clip = tmp_path / "shot_02.mp4"
    assert next_attempt_path(clip).name == "shot_02.attempt1.mp4"
    (tmp_path / "shot_02.attempt1.mp4").write_bytes(b"")
    assert next_attempt_path(clip).name == "shot_02.attempt2.mp4"


def test_append_spend_keeps_free_refusals_and_totals_only_billed(tmp_path):
    log = tmp_path / "artifacts" / "spend_log.json"
    append_spend(log, {"shot": "shot_02", "outcome": OUTPUT_LIKENESS_FILTER, "usd": 0.0})
    total = append_spend(log, {"shot": "shot_02", "outcome": "succeeded", "usd": 0.6208})
    assert total == 0.6208
    saved = json.loads(log.read_text())
    assert [a["outcome"] for a in saved["attempts"]] == [OUTPUT_LIKENESS_FILTER, "succeeded"]


def test_actual_usd_matches_the_billed_shot():
    # shot_02, 58,045 tokens at 77 CNY per million and 7.2 CNY per USD
    assert actual_usd(58045, 77.0, 7.2) == 0.6208
    assert actual_usd(None, 77.0, 7.2) == 0.0


# ---- the paid path, driven by a fake tool: no network, no spend ----

class _Result:
    def __init__(self, success, error=None, data=None):
        self.success, self.error, self.data = success, error, data or {}


def _fake_tool(outcomes, calls):
    class FakeArk:
        def _resolve_model(self, inputs):
            return "dreamina-seedance-2-5-260628", "2.5"

        def estimate_token_usage(self, inputs):
            return 60264

        def estimate_cost(self, inputs):
            return 0.6445

        def _build_payload(self, inputs):
            return {}

        @staticmethod
        def _get_cny_per_usd():
            return 7.2

        def execute(self, inputs):
            calls.append(inputs["output_path"])
            outcome = outcomes.pop(0)
            if outcome == "ok":
                from pathlib import Path
                Path(inputs["output_path"]).write_bytes(b"new take")
                return _Result(True, data={"task_id": "cgt-1", "video_url": "https://ark.example/v.mp4",
                                           "usage": {"completion_tokens": 58045}})
            return _Result(False, error=outcome, data={"task_id": "cgt-0", "status": "failed"})

    return FakeArk


@pytest.fixture
def paid(tmp_path, monkeypatch):
    import lib.env_loader
    import tools.video.seedance_ark as ark

    contract = _project(tmp_path, {"id": "shot_02", "seconds": 6})
    (tmp_path / "artifacts" / "shot_contract.json").write_text(json.dumps(contract))
    monkeypatch.setattr(lib.env_loader, "load_env", lambda root=None: None)
    calls = []

    def arm(outcomes):
        monkeypatch.setattr(ark, "SeedanceArkVideo", _fake_tool(list(outcomes), calls))
        return calls

    return tmp_path, arm


def _attempts(project):
    return json.loads((project / "artifacts" / "spend_log.json").read_text())


def test_plan_only_never_calls_the_provider(paid):
    from scripts.om_shot import run_shot
    project, arm = paid
    calls = arm(["ok"])
    assert run_shot(project, "shot_02", go=False, filter_retries=1) == 0
    assert calls == [] and not (project / "artifacts" / "spend_log.json").exists()


def test_a_likeness_refusal_is_retried_unchanged_and_both_attempts_are_logged(paid):
    from scripts.om_shot import run_shot
    project, arm = paid
    calls = arm([LIKENESS_REFUSAL, "ok"])
    assert run_shot(project, "shot_02", go=True, filter_retries=1) == 0
    assert len(calls) == 2
    log = _attempts(project)
    assert [a["outcome"] for a in log["attempts"]] == [OUTPUT_LIKENESS_FILTER, "succeeded"]
    assert [a["usd"] for a in log["attempts"]] == [0.0, 0.6208]
    assert log["total_usd"] == 0.6208


def test_two_likeness_refusals_stop_and_hand_back_to_the_user(paid):
    from scripts.om_shot import EXIT_LIKENESS_EXHAUSTED, run_shot
    project, arm = paid
    calls = arm([LIKENESS_REFUSAL, LIKENESS_REFUSAL, "ok"])
    assert run_shot(project, "shot_02", go=True, filter_retries=1) == EXIT_LIKENESS_EXHAUSTED
    assert len(calls) == 2  # the third roll is the user's decision, not the script's
    assert _attempts(project)["total_usd"] == 0.0


def test_a_refused_person_image_is_never_retried(paid):
    from scripts.om_shot import EXIT_PERSON_REFUSED, run_shot
    project, arm = paid
    calls = arm([PERSON_REFUSAL, "ok"])
    assert run_shot(project, "shot_02", go=True, filter_retries=3) == EXIT_PERSON_REFUSED
    assert len(calls) == 1


def test_a_retake_keeps_the_previous_take(paid):
    from scripts.om_shot import run_shot
    project, arm = paid
    video = project / "assets" / "video"
    video.mkdir(parents=True)
    (video / "shot_02.mp4").write_bytes(b"old take")
    arm(["ok"])
    assert run_shot(project, "shot_02", go=True, filter_retries=1) == 0
    assert (video / "shot_02.attempt1.mp4").read_bytes() == b"old take"
    assert (video / "shot_02.mp4").read_bytes() == b"new take"


# ---- stopping rules ----

def _set_contract(project, **keys):
    path = project / "artifacts" / "shot_contract.json"
    contract = json.loads(path.read_text())
    contract.update(keys)
    path.write_text(json.dumps(contract))


def test_the_budget_stops_a_take_before_it_is_paid_for(paid):
    from scripts.om_shot import EXIT_LIMIT_REACHED, run_shot
    project, arm = paid
    _set_contract(project, budget_usd=1.0)
    calls = arm(["ok", "ok"])
    assert run_shot(project, "shot_02", go=True, filter_retries=1) == 0  # 0.6208 spent
    assert run_shot(project, "shot_02", go=True, filter_retries=1) == EXIT_LIMIT_REACHED
    assert len(calls) == 1
    assert (project / "assets" / "video" / "shot_02.mp4").read_bytes() == b"new take"  # untouched


def test_the_attempt_cap_counts_billed_takes_and_ignores_free_refusals(paid):
    from scripts.om_shot import EXIT_LIMIT_REACHED, run_shot
    project, arm = paid
    _set_contract(project, max_attempts_per_shot=2)
    calls = arm([LIKENESS_REFUSAL, "ok", "ok", "ok"])
    assert run_shot(project, "shot_02", go=True, filter_retries=1) == 0
    assert run_shot(project, "shot_02", go=True, filter_retries=1) == 0
    assert run_shot(project, "shot_02", go=True, filter_retries=1) == EXIT_LIMIT_REACHED
    assert len(calls) == 3


def test_a_finished_clip_logs_its_url_and_when_it_dies(paid):
    from scripts.om_shot import run_shot
    project, arm = paid
    arm(["ok"])
    assert run_shot(project, "shot_02", go=True, filter_retries=1) == 0
    entry = _attempts(project)["attempts"][-1]
    assert entry["video_url"] == "https://ark.example/v.mp4"
    assert entry["urls_expire"] > entry["at"]
