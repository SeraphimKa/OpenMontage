"""`om_shot.py --sheet` audio evidence: stream facts, silences and a transcript a reviewer can read."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import om_shot

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _clip(path: Path, *, audio: bool, seconds: float = 3.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={seconds}"]
    if audio:
        # one second of tone, then silence: one silence gap the detector must find
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                 "-af", "volume='if(lt(t,1),1,0)':eval=frame", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(args, check=True)
    return path


class _Result:
    def __init__(self, success, data=None, error=None):
        self.success, self.data, self.error = success, data, error


class _StubTranscriber:
    """Stands in for faster-whisper so the test downloads no model."""
    calls: list[dict] = []
    result = _Result(True, {"language": "el", "model_size": "small", "device": "cpu",
                            "segments": [{"start": 0.1, "end": 0.9, "text": " Πώς μπήκες; "}],
                            "word_timestamps": [{"start": 0.1, "end": 0.5, "word": "Πώς"},
                                                {"start": 0.5, "end": 0.9, "word": "μπήκες;"}]})

    def execute(self, inputs):
        self.calls.append(inputs)
        return self.result


@pytest.fixture
def stub(monkeypatch):
    import tools.analysis.transcriber as mod
    _StubTranscriber.calls = []
    _StubTranscriber.result = _StubTranscriber.result
    monkeypatch.setattr(mod, "Transcriber", _StubTranscriber)
    return _StubTranscriber


def test_silent_file_is_reported_as_no_stream(tmp_path, stub):
    clip = _clip(tmp_path / "shot_01.mp4", audio=False)
    out = tmp_path / "adherence"; out.mkdir()
    evidence = om_shot.audio_evidence(clip, out, "shot_01")
    assert evidence["audio_stream"] is False
    assert stub.calls == []
    assert "no audio stream" in (out / "shot_01_audio.txt").read_text()


def test_stream_silences_and_transcript_are_written(tmp_path, stub):
    clip = _clip(tmp_path / "shot_01.mp4", audio=True)
    out = tmp_path / "adherence"; out.mkdir()
    evidence = om_shot.audio_evidence(clip, out, "shot_01", language="el")
    assert evidence["audio_stream"] is True and evidence["codec"] == "aac"
    assert evidence["mean_volume_db"] is not None
    assert evidence["silences"] and evidence["silences"][0]["start"] >= 0.9
    assert stub.calls[0]["language"] == "el" and stub.calls[0]["output_dir"] == str(out)
    assert evidence["language"] == "el"
    assert evidence["segments"] == [{"start": 0.1, "end": 0.9, "text": "Πώς μπήκες;"}]
    assert [w["word"] for w in evidence["words"]] == ["Πώς", "μπήκες;"]
    text = (out / "shot_01_audio.txt").read_text()
    assert "Πώς μπήκες;" in text and "silence" in text and "not of accent or lip-sync" in text
    assert json.loads((out / "shot_01_audio.json").read_text())["shot_id"] == "shot_01"


def test_transcriber_failure_is_recorded_not_raised(tmp_path, stub):
    stub.result = _Result(False, error="model download failed")
    clip = _clip(tmp_path / "shot_01.mp4", audio=True)
    out = tmp_path / "adherence"; out.mkdir()
    evidence = om_shot.audio_evidence(clip, out, "shot_01")
    assert evidence["audio_stream"] is True
    assert evidence["transcriber_error"] == "model download failed"
    assert "transcript unavailable" in (out / "shot_01_audio.txt").read_text()


def test_make_sheets_writes_the_audio_files_too(tmp_path, stub):
    project = tmp_path / "p"
    _clip(project / "assets" / "video" / "shot_01.mp4", audio=True)
    (project / "artifacts").mkdir(parents=True)
    (project / "artifacts" / "shot_contract.json").write_text(json.dumps(
        {"dialogue_language": "el", "shots": [{"id": "shot_01", "seconds": 4}]}))
    assert om_shot.make_sheets(project, "shot_01") == 0
    out = project / "artifacts" / "adherence"
    assert (out / "shot_01_sheet.png").exists() and (out / "shot_01_audio.json").exists()
    assert stub.calls[0]["language"] == "el"
