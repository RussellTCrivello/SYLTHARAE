"""Report what the installed OCR engines actually do, as GitHub annotations.

    python tools/ci/ocr_selfcheck.py

Tesseract is a system package (see docs/INSTALL.md), so its behaviour depends
on the host rather than on requirements.txt. This prints one ::notice:: per
check (version, languages, word confidences, rotated text) so that a CI run
records the engine the tests ran against, and a ::warning:: when a check
cannot run. Exit status is always 0: the tests decide the job.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _escape(text):
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _notice(title, text):
    print(f"::notice title={title}::{_escape(text)[:4000]}")


def _warning(title, text):
    print(f"::warning title={title}::{_escape(text)[:4000]}")


def _sample(text, size=(1000, 300)):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((60, 100), text, fill="black")
    return img


def main():
    try:
        import pytesseract

        _notice("tesseract version", pytesseract.get_tesseract_version())
        _notice("tesseract languages", ", ".join(sorted(pytesseract.get_languages(config=""))))
    except Exception as exc:  # the check reports; it never fails the job
        _warning("tesseract unavailable", f"{type(exc).__name__}: {exc}")
        return 0

    img = _sample("OCR SELF CHECK 2026")
    try:
        data = pytesseract.image_to_data(
            img, lang="eng", config="--oem 3 --psm 6", output_type=pytesseract.Output.DICT
        )
        words = [(w, c) for w, c in zip(data.get("text", []), data.get("conf", [])) if str(w).strip()]
        _notice("tesseract image_to_data", f"words={words!r} conf types={sorted({type(c).__name__ for c in data.get('conf', [])})}")
    except Exception as exc:
        _warning("tesseract image_to_data raised", f"{type(exc).__name__}: {exc}")

    from core.ocr.engines import TesseractEngine, recognize_image, get_ocr_engine

    engine = TesseractEngine()
    result = engine.recognize(img, ["eng"])
    _notice(
        "TesseractEngine.recognize",
        f"text={result.text!r} blocks={len(result.blocks)} mean_confidence={result.mean_confidence!r} error={result.error!r}",
    )
    selected = get_ocr_engine()
    _notice("selected OCR engine", selected.name if selected else "none")
    for angle in (90, 180, 270):
        rotated = _sample(f"ROTATEDIMAGE_{angle}_9911").rotate(angle, expand=True)
        res = recognize_image(rotated)
        _notice(
            f"recognize_image rotated {angle}",
            f"text={res.text!r} mean_confidence={res.mean_confidence!r}",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
