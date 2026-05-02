"""
Small Gemini screenshot reasoning smoke test.

This checks two paths:
1. The current agent compatibility path used by GeminiPageAnalyzer.
2. A direct google-genai vision call with inline image data.

Expected: direct_vision should read the word in the generated screenshot.
If agent_compat cannot, the agent's screenshot-analysis path is not actually
passing image pixels to Gemini.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "Agents"))

from gemini_compat import genai  # noqa: E402


SECRET_WORD = "ORANGE"
MODEL = "gemini-2.5-flash"


def make_test_image() -> bytes:
    image = Image.new("RGB", (1200, 360), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default()

    draw.rectangle((40, 40, 1160, 320), outline="black", width=4)
    draw.text((80, 130), f"SECRET WORD: {SECRET_WORD}", fill="black", font=font)

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def parse_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return {"raw": text}
    return json.loads(text[start:end])


def run_agent_compat_path(image_bytes: bytes) -> dict:
    image = Image.open(BytesIO(image_bytes))
    prompt = (
        "Look only at the image. Return JSON only: "
        '{"word":"<the secret word visible in the image, or UNKNOWN>",'
        '"can_see_image":true/false,"reason":"short"}'
    )
    model = genai.GenerativeModel(MODEL)
    response = model.generate_content([image, prompt])
    result = parse_json(response.text or "")
    result["raw_response"] = response.text
    return result


def run_direct_vision_path(image_bytes: bytes) -> dict:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)
    screenshot_b64 = base64.b64encode(image_bytes).decode("ascii")
    prompt = (
        "Look only at the image. Return JSON only: "
        '{"word":"<the secret word visible in the image, or UNKNOWN>",'
        '"can_see_image":true/false,"reason":"short"}'
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": screenshot_b64}},
                    {"text": prompt},
                ],
            }
        ],
    )
    result = parse_json(response.text or "")
    result["raw_response"] = response.text
    return result


def main() -> int:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("SKIP: GOOGLE_API_KEY/GEMINI_API_KEY is not set.")
        return 2

    # google-genai may prefer GOOGLE_API_KEY from the process environment when
    # both names are present. Keep this smoke test focused on image handling.
    os.environ["GOOGLE_API_KEY"] = api_key
    os.environ["GEMINI_API_KEY"] = api_key
    genai.configure(api_key=api_key)

    image_bytes = make_test_image()
    compat = run_agent_compat_path(image_bytes)
    direct = run_direct_vision_path(image_bytes)

    print("agent_compat:", json.dumps(compat, indent=2))
    print("direct_vision:", json.dumps(direct, indent=2))

    compat_ok = str(compat.get("word", "")).upper() == SECRET_WORD
    direct_ok = str(direct.get("word", "")).upper() == SECRET_WORD

    if direct_ok and not compat_ok:
        print("RESULT: direct Gemini vision works, but the current agent compat path does not pass image pixels.")
        return 1
    if direct_ok and compat_ok:
        print("RESULT: Gemini screenshot reasoning works through both paths.")
        return 0
    print("RESULT: Gemini screenshot reasoning did not pass the direct vision control test.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
