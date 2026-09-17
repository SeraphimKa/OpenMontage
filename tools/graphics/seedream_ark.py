"""Seedream image generation and editing on BytePlus / Volcengine Ark.

Uses the same ARK_API_KEY and ARK_BASE_URL as seedance_ark, so a team that can
generate video needs no second key to generate images. The model must be
activated once in the Ark console; an inactive model is refused for free with
`ModelNotOpen`.

The image is saved byte for byte as the provider served it. That matters for
faces: Ark trusts a face-bearing image only as the original file generated on
the same account, for 30 days, so this tool never decodes, resizes or re-encodes
what it downloads, and it reports `trust_expires` for the record.
"""
from __future__ import annotations

import base64
import mimetypes
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

SAME_ACCOUNT_TRUST = timedelta(days=30)
OUTPUT_URL_LIFETIME = timedelta(hours=24)


class SeedreamArkImage(BaseTool):
    name = "seedream_ark"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "ark"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
    DEFAULT_MODEL = "seedream-5-0-260128"
    MAX_REFERENCE_IMAGES = 10

    dependencies = ["env:ARK_API_KEY"]
    install_instructions = (
        "Set ARK_API_KEY (the same key seedance_ark uses) and activate the Seedream\n"
        "model in the Ark console under model activation. Optional: ARK_SEEDREAM_MODEL,\n"
        "ARK_BASE_URL, ARK_SEEDREAM_USD_PER_IMAGE."
    )
    agent_skills = ["visual-style", "open-montage"]

    capabilities = ["generate_image", "text_to_image", "image_to_image", "edit_image"]
    supports = {
        "image_edit": True,
        "reference_images": True,
        "custom_size": True,
        "aspect_ratio": True,
        "text_rendering": True,
        "ark_trusted_faces": True,
    }
    best_for = [
        "reference plates and face sheets for seedance_ark (same-account outputs are trusted for 30 days)",
        "editing a held image while keeping the rest of it",
        "teams that already hold an Ark key and want no second provider",
    ]
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string", "description": "Ark model ID; default seedream-5-0-260128."},
            "size": {
                "type": "string",
                "default": "2K",
                "description": "'1K', '2K', '4K', or exact pixels such as '2848x1600'.",
            },
            "aspect_ratio": {
                "type": "string",
                "description": "Used only when size is 1K/2K/4K: appended to the prompt as the vendor documents.",
            },
            "image_path": {"type": "string"},
            "image_url": {"type": "string"},
            "image_paths": {"type": "array", "items": {"type": "string"}},
            "image_urls": {"type": "array", "items": {"type": "string"}},
            "watermark": {"type": "boolean", "default": False},
            "seed": {"type": "integer"},
            "output_path": {"type": "string"},
        },
    }
    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=100, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["prompt", "model", "size", "aspect_ratio", "image_path", "image_url",
                              "image_paths", "image_urls", "seed"]
    side_effects = ["writes image file to output_path", "calls the Ark images API (billed per image)"]
    user_visible_verification = ["Inspect the image against the prompt before using it as a reference"]

    def _get_api_key(self) -> str | None:
        return os.environ.get("ARK_API_KEY")

    def _get_base_url(self) -> str:
        base_url = os.environ.get("ARK_BASE_URL", self.BASE_URL).rstrip("/")
        if not base_url.startswith("https://"):
            raise ValueError("ARK_BASE_URL must be an https:// URL")
        return base_url

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # The vendor bills per generated image. 0.035 USD is this team's working
        # figure for Seedream 5.0, not a verified price: set the env var from the bill.
        return round(float(os.environ.get("ARK_SEEDREAM_USD_PER_IMAGE", "0.035")), 4)

    @staticmethod
    def _as_image_field(path: str) -> str:
        file = Path(path)
        if not file.is_file():
            raise ValueError(f"reference image not found: {file}")
        mime = mimetypes.guess_type(file.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(file.read_bytes()).decode()}"

    def _build_payload(self, inputs: dict[str, Any]) -> dict[str, Any]:
        prompt = str(inputs.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        size = str(inputs.get("size", "2K"))
        if inputs.get("aspect_ratio") and "x" not in size.lower():
            prompt = f"{prompt} Aspect ratio {inputs['aspect_ratio']}."
        payload: dict[str, Any] = {
            "model": inputs.get("model") or os.environ.get("ARK_SEEDREAM_MODEL", self.DEFAULT_MODEL),
            "prompt": prompt,
            "size": size,
            "response_format": "url",
            "watermark": bool(inputs.get("watermark", False)),
        }
        if isinstance(inputs.get("seed"), int):
            payload["seed"] = inputs["seed"]

        images = list(inputs.get("image_urls") or [])
        if inputs.get("image_url"):
            images.append(inputs["image_url"])
        paths = list(inputs.get("image_paths") or [])
        if inputs.get("image_path"):
            paths.append(inputs["image_path"])
        images += [self._as_image_field(path) for path in paths]
        if len(images) > self.MAX_REFERENCE_IMAGES:
            raise ValueError(f"at most {self.MAX_REFERENCE_IMAGES} reference images")
        if images:
            payload["image"] = images[0] if len(images) == 1 else images
        return payload

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        import requests

        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(success=False, error="ARK_API_KEY not set. " + self.install_instructions)

        start = time.time()
        try:
            payload = self._build_payload(inputs)
            response = requests.post(
                f"{self._get_base_url()}/images/generations",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=(10, 300),
            )
            if response.status_code >= 400:
                error = (response.json().get("error") or {}) if response.content else {}
                raise RuntimeError(f"{error.get('code', response.status_code)}: {error.get('message', response.text[:300])}")
            body = response.json()
            image_url = ((body.get("data") or [{}])[0]).get("url")
            if not image_url:
                raise RuntimeError(f"Ark returned no image: {str(body)[:300]}")

            suffix = Path(image_url.split("?")[0]).suffix or ".jpeg"
            output_path = Path(inputs.get("output_path") or f"seedream_ark{suffix}")
            if output_path.suffix.lower() not in (suffix.lower(), ".jpg" if suffix == ".jpeg" else suffix):
                # Keep the provider's format: converting it would void Ark's trust in a face.
                output_path = output_path.with_suffix(suffix)
            download = requests.get(image_url, timeout=120)
            download.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(download.content)
        except Exception as exc:
            return ToolResult(success=False, error=f"Seedream on Ark failed: {exc}")

        now = datetime.now(timezone.utc)
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": body.get("model", payload["model"]),
                "prompt": payload["prompt"],
                "operation": "image_to_image" if "image" in payload else "text_to_image",
                "output": str(output_path),
                "output_path": str(output_path),
                "size": (body["data"][0]).get("size"),
                "image_url": image_url,
                "url_expires": (now + OUTPUT_URL_LIFETIME).isoformat(timespec="seconds"),
                "generated_at": now.isoformat(timespec="seconds"),
                "trust_expires": (now + SAME_ACCOUNT_TRUST).date().isoformat(),
                "usage": body.get("usage") or {},
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=payload["model"],
        )
