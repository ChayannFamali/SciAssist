"""Extract figures from PDF and describe them via VLM."""
import io
import json
import re
from pathlib import Path

import fitz
from loguru import logger
from PIL import Image

from sciassist.config import get_settings, get_yaml_config
from sciassist.router.model_router import ModelRouter
from sciassist.utils.lm_studio_client import LMStudioClient


def _parse_json(text: str) -> dict:
    """Extract JSON from LLM response (handles ```json blocks)."""
    for pattern in (r"```(?:json)?\s*(\{.*?\})\s*```", r"(\{.*\})"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    return {}


def _find_caption(text: str, fig_num: int) -> str:
    """Try to find 'Figure N' caption in paper text."""
    patterns = [
        rf"(?:Figure|Fig\.?)\s+{fig_num}[:\.]?\s*([^\n]{{10,200}})",
        rf"(?:Рис\.?|Рисунок)\s+{fig_num}[:\.]?\s*([^\n]{{10,200}})",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(0)[:200]
    return ""


def _load_prompt() -> str:
    p = get_settings().project_root / "configs" / "prompts" / "figure_description.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else (
        'Describe this scientific figure. Return JSON: '
        '{"type":"...","vlm_description":"...","main_finding":"...","axis_x":null,"axis_y":null}'
    )


async def extract_figures(pdf_path: Path, citekey: str) -> Path:
    """
    Extract figures from PDF, describe via VLM, save figures.json + PNGs.
    Returns path to figures.json.
    """
    cfg = get_settings()
    yaml_cfg = get_yaml_config()
    min_px: int = yaml_cfg.get("vision", {}).get("min_figure_size_px", 200)

    out_dir = cfg.extracted_figures_path / citekey
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    full_text = " ".join(page.get_text() for page in doc)

    router = ModelRouter()
    spec = router.select("vision")
    client = LMStudioClient()
    prompt = _load_prompt()

    figures = []
    fig_idx = 0
    seen_xrefs: set[int] = set()   # avoid duplicate images across pages

    for page_num, page in enumerate(doc):
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            try:
                base = doc.extract_image(xref)
                w, h = base["width"], base["height"]
            except Exception:
                continue

            if w < min_px or h < min_px:
                continue

            fig_id = f"fig_{fig_idx:03d}"
            img_path = out_dir / f"{fig_id}.png"

            # Convert to PNG via Pillow (handles JPEG, CMYK, etc.)
            try:
                img = Image.open(io.BytesIO(base["image"])).convert("RGB")

                # Resize if too large for VLM context
                MAX_VLM_DIM = 1024
                if max(w, h) > MAX_VLM_DIM:
                    ratio = MAX_VLM_DIM / max(w, h)
                    img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

                img.save(img_path, "PNG")
            except Exception as e:
                logger.warning(f"{citekey} {fig_id}: image convert failed: {e}")
                continue

            fig_idx += 1
            caption = _find_caption(full_text, fig_idx)

            # VLM description
            vlm_data: dict = {}
            try:
                resp = await client.vision_chat([img_path], prompt, spec.name)
                vlm_data = _parse_json(resp)
            except Exception as e:
                logger.warning(f"{citekey} {fig_id}: VLM failed: {e}")

            figures.append({
                "figure_id": fig_id,
                "page": page_num + 1,
                "path": f"{fig_id}.png",
                "caption_detected": caption,
                "type": vlm_data.get("type", "unknown"),
                "vlm_description": vlm_data.get("vlm_description", ""),
                "main_finding": vlm_data.get("main_finding", ""),
                "axis_x": vlm_data.get("axis_x"),
                "axis_y": vlm_data.get("axis_y"),
            })
            logger.debug(f"{citekey}: {fig_id} ({w}×{h}px) page={page_num + 1}")

    doc.close()

    out = out_dir / "figures.json"
    out.write_text(json.dumps(figures, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"{citekey}: {len(figures)} фигур → {out}")
    return out
