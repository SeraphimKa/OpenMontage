"""Contract tests for Seedream on Ark. No network: requests is faked."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.graphics.seedream_ark import SeedreamArkImage

MODEL_NOT_OPEN = {"error": {"code": "ModelNotOpen", "message": "Your account 2100000000 has not activated the model "
                            "seedream-5-0-260128. Please activate the model service in the Ark Console."}}


def test_registry_discovers_it_and_it_shares_the_ark_key(monkeypatch, isolated_tool_registry):
    isolated_tool_registry.discover("tools")  # discovery loads .env, so clear the key after it
    tool = isolated_tool_registry.get("seedream_ark")
    assert tool is not None and tool.capability == "image_generation"
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    assert tool.get_status().value == "unavailable"
    monkeypatch.setenv("ARK_API_KEY", "k")
    assert tool.get_status().value == "available"
    assert tool.supports["image_edit"] is True


def test_text_to_image_payload():
    payload = SeedreamArkImage()._build_payload({"prompt": "an empty bed", "aspect_ratio": "16:9"})
    assert payload["model"] == "seedream-5-0-260128"
    assert payload["size"] == "2K" and payload["watermark"] is False
    assert payload["prompt"].endswith("Aspect ratio 16:9.")
    assert "image" not in payload


def test_edit_payload_sends_one_image_as_a_string_and_several_as_a_list(tmp_path):
    held = tmp_path / "room.png"
    held.write_bytes(b"\x89PNG")
    tool = SeedreamArkImage()
    one = tool._build_payload({"prompt": "make the blanket plaid", "image_path": str(held)})
    assert one["image"].startswith("data:image/png;base64,")
    two = tool._build_payload({"prompt": "x", "image_path": str(held), "image_urls": ["https://a/b.jpeg"]})
    assert two["image"][0] == "https://a/b.jpeg" and len(two["image"]) == 2
    with pytest.raises(ValueError, match="not found"):
        tool._build_payload({"prompt": "x", "image_path": str(tmp_path / "missing.png")})


class _Response:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content, self.text = status, body, content or b"{}", str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


def _fake_requests(monkeypatch, post, get=None):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: post)
    monkeypatch.setattr(requests, "get", lambda *a, **k: get)


def test_an_inactive_model_is_named_as_such(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "k")
    _fake_requests(monkeypatch, _Response(404, MODEL_NOT_OPEN))
    result = SeedreamArkImage().execute({"prompt": "x"})
    assert not result.success and "ModelNotOpen" in result.error and "Ark Console" in result.error


def test_the_image_is_saved_byte_for_byte_in_the_providers_format(monkeypatch, tmp_path):
    monkeypatch.setenv("ARK_API_KEY", "k")
    served = b"\xff\xd8 exactly what Ark served"
    body = {"model": "seedream-5-0-260128", "data": [{"url": "https://tos/x_0.jpeg?sig=1", "size": "2848x1600"}],
            "usage": {"generated_images": 1}}
    _fake_requests(monkeypatch, _Response(200, body), _Response(200, content=served))
    result = SeedreamArkImage().execute({"prompt": "x", "output_path": str(tmp_path / "face.png")})
    assert result.success
    saved = Path(result.data["output_path"])
    assert saved.suffix == ".jpeg" and saved.read_bytes() == served  # never re-encoded to the asked .png
    assert result.data["trust_expires"] > result.data["generated_at"][:10]
    assert result.data["operation"] == "text_to_image"
