"""Degradation for the moderate and hard tiers: a clean PDF made to look scanned or photographed.

Pillow only (Tier 2 Block 2 decision). Augraphy and OpenCV would add paper texture and ink
bleed, at the cost of numpy, opencv and a library whose Python 3.13 support was unverified
- and harder seeding. What Pillow gives is the part that tests a vision model: rotation,
blur, resolution loss, JPEG artefacts, uneven light and, for the hard tier, perspective.

Two halves, deliberately separate:

- `params_for` decides every parameter from the document's own labelled stream
  (`doc_rng(seed, doc_id, "degrade")`). The parameters go into the answer key's
  `degradation` before anything is rendered, so the key says exactly what was done.
- `apply` performs them on a rasterised page. Given the same parameters and the same input
  pixels it produces the same output pixels, so a degraded image is reproducible from the
  key alone (spec 1.4).

Readability is checked by eye on a sample (spec 6.2). If an effect makes text unreadable
the RANGES here are tuned - never the key, and never a single document.
"""

from pathlib import Path

from carbon_r.synthgen.rng import doc_rng

# moderate: an office scanner
SCAN_DPI = 150
# Narrowed twice from (0.5, 2.0) and (1.0, 3.0): on the wide label/value layouts, 2-3
# degrees tilted each right-hand value down a whole row, and 1.5 still half a row, so a
# human could not pair "Units used" with its figure (elec_089). Spec 6.2: tune the range,
# never the key.
SCAN_ROTATE_DEG = (0.2, 0.8)
SCAN_BLUR = (0.3, 0.7)
SCAN_JPEG_QUALITY = (55, 80)
SCAN_LIGHTING = (0.06, 0.15)

# hard: a phone photograph of the page on a desk
PHOTO_DPI = 150
PHOTO_ROTATE_DEG = (0.3, 1.0)
PHOTO_BLUR = (0.4, 0.9)
PHOTO_DOWNSCALE = (0.70, 0.85)
PHOTO_JPEG_QUALITY = (40, 60)
PHOTO_SHADOW = (0.22, 0.38)
PHOTO_KEYSTONE = (0.015, 0.04)  # fraction of width the top edge is pulled in, per side

LIGHT_DIRECTIONS = ("left", "right", "top", "bottom")
SCAN_SHARE_OF_MODERATE = 0.5


def _r(value: float) -> float:
    return round(value, 3)


def params_for(seed: int, doc_id: str, tier: str) -> dict:
    """Every degradation parameter for one document, decided before rendering.

    `{}` for clean. A moderate document is either left a crisp PDF (`rendering: pdf`) or
    scanned; a hard one is always photographed.
    """
    if tier == "clean":
        return {}
    rng = doc_rng(seed, doc_id, "degrade")
    sign = 1 if rng.random() < 0.5 else -1

    if tier == "moderate":
        if rng.random() >= SCAN_SHARE_OF_MODERATE:
            return {"rendering": "pdf"}
        return {
            "rendering": "scan",
            "dpi": SCAN_DPI,
            "rotate_deg": _r(sign * rng.uniform(*SCAN_ROTATE_DEG)),
            "blur_radius": _r(rng.uniform(*SCAN_BLUR)),
            "lighting": {
                "direction": rng.choice(LIGHT_DIRECTIONS),
                "strength": _r(rng.uniform(*SCAN_LIGHTING)),
            },
            "jpeg_quality": rng.randint(*SCAN_JPEG_QUALITY),
        }

    if tier == "hard":
        return {
            "rendering": "photo",
            "dpi": PHOTO_DPI,
            "keystone": _r(rng.uniform(*PHOTO_KEYSTONE)),
            "rotate_deg": _r(sign * rng.uniform(*PHOTO_ROTATE_DEG)),
            "shadow": {
                "direction": rng.choice(LIGHT_DIRECTIONS),
                "strength": _r(rng.uniform(*PHOTO_SHADOW)),
            },
            "blur_radius": _r(rng.uniform(*PHOTO_BLUR)),
            "downscale": _r(rng.uniform(*PHOTO_DOWNSCALE)),
            "jpeg_quality": rng.randint(*PHOTO_JPEG_QUALITY),
        }

    raise ValueError(f"unknown difficulty tier {tier!r}")


def is_image(params: dict) -> bool:
    """True when the tier's document is a degraded image rather than the clean PDF."""
    return params.get("rendering") in ("scan", "photo")


def _gradient(size: tuple[int, int], direction: str, strength: float):
    """A greyscale mask, 255 on the lit side falling to 255 x (1 - strength)."""
    from PIL import Image

    width, height = size
    length = width if direction in ("left", "right") else height
    ramp = Image.linear_gradient("L").resize((1, 256))
    values = [round(255 * (1 - strength * (i / 255))) for i in range(256)]
    ramp = ramp.point(values)
    if direction in ("left", "right"):
        ramp = ramp.rotate(90, expand=True)  # horizontal: 256 x 1
        mask = ramp.resize((length, height))
        if direction == "right":
            mask = mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    else:
        mask = ramp.resize((width, length))
        if direction == "bottom":
            mask = mask.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return mask


def _darken(image, direction: str, strength: float):
    from PIL import ImageChops

    mask = _gradient(image.size, direction, strength).convert("RGB")
    return ImageChops.multiply(image, mask)


def _keystone(image, pull: float):
    """Pull the top edge in, as a phone held below the page sees it."""
    from PIL import Image

    width, height = image.size
    inset = pull * width
    # Image.transform QUAD maps the output rectangle from this quadrilateral of the input
    # (upper-left, lower-left, lower-right, upper-right). Sampling a WIDER top band makes
    # the page's top edge appear narrower in the output.
    quad = (-inset, 0, 0, height, width, height, width + inset, 0)
    return image.transform(
        image.size, Image.Transform.QUAD, quad, Image.Resampling.BICUBIC, fillcolor="#d8d4cc"
    )


def apply(image, params: dict):
    """Degrade one RGB page image according to `params`. Deterministic."""
    from PIL import Image, ImageFilter

    image = image.convert("RGB")
    rendering = params.get("rendering")
    if rendering == "scan":
        image = _darken(image, params["lighting"]["direction"], params["lighting"]["strength"])
        image = image.rotate(
            params["rotate_deg"], Image.Resampling.BICUBIC, expand=True, fillcolor="white"
        )
        image = image.filter(ImageFilter.GaussianBlur(params["blur_radius"]))
    elif rendering == "photo":
        image = _keystone(image, params["keystone"])
        image = _darken(image, params["shadow"]["direction"], params["shadow"]["strength"])
        image = image.rotate(
            params["rotate_deg"], Image.Resampling.BICUBIC, expand=True, fillcolor="#d8d4cc"
        )
        image = image.filter(ImageFilter.GaussianBlur(params["blur_radius"]))
        width, height = image.size
        image = image.resize(
            (round(width * params["downscale"]), round(height * params["downscale"])),
            Image.Resampling.LANCZOS,
        )
    else:
        raise ValueError(f"nothing to apply for rendering {rendering!r}")
    return image


def jpeg_roundtrip(image, quality: int):
    """Re-encode through JPEG at `quality` and decode again: the compression artefacts."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def degrade_pdf(pdf: Path, out_png: Path, params: dict) -> Path:
    """Rasterise a clean PDF's first page, degrade it, and write a PNG. Returns the path."""
    import subprocess
    import tempfile

    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "page"
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                str(params["dpi"]),
                "-png",
                "-f",
                "1",
                "-l",
                "1",
                str(pdf),
                str(prefix),
            ],
            check=True,
            capture_output=True,
        )
        rendered = sorted(Path(tmp).glob("page*.png"))
        if not rendered:
            raise RuntimeError(f"pdftoppm produced no page for {pdf}")
        with Image.open(rendered[0]) as page:
            image = apply(page, params)
        image = jpeg_roundtrip(image, params["jpeg_quality"])
        image.save(out_png, format="PNG", optimize=False)
    return out_png


__all__ = ["apply", "degrade_pdf", "is_image", "jpeg_roundtrip", "params_for"]
