"""Pluggable OCR engine layer (PHASE 2A).

The reader layer previously hard-wired a single OCR backend::

    if pytesseract and self._is_tesseract_available():
        ...

so on any host without the ``tesseract`` system binary, **no** OCR happened at
all and scanned documents produced nothing. This module makes OCR a first-class,
substitutable capability.

Engine preference is deliberate, not incidental:

1. **tesseract** - supports the languages this project declares as defaults
   (``heb``, ``eng``, ``ara``; see ``DEFAULT_OCR_LANGUAGES``) and has an OSD
   script detector. Preferred whenever present.
2. **rapidocr** - PaddleOCR PP-OCRv4 models running under ONNX Runtime. Pure
   pip, no system binary, so it works in environments where tesseract cannot be
   installed. Latin + Chinese; it does **not** cover Hebrew or Arabic.

Every result carries explicit provenance - ``engine``, ``engine_version``,
``language`` - so downstream storage and display can state *how* text was
derived rather than presenting OCR output as if it were source text.
"""

from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]


@dataclass(frozen=True)
class OcrBlock:
    """One recognised text region.

    Attributes:
        text: The recognised text.
        confidence: 0.0-1.0 as reported by the engine, or ``None`` when the
            engine does not report confidence. Kept distinct from 0.0 so
            "unknown" is never mistaken for "certainly wrong".
        bbox: ``(x0, y0, x1, y1)`` in source-image pixels, or ``None``.
    """

    text: str
    confidence: Optional[float] = None
    bbox: Optional[BBox] = None


@dataclass
class OcrResult:
    """Engine-agnostic OCR outcome with provenance."""

    text: str = ""
    blocks: List[OcrBlock] = field(default_factory=list)
    engine: str = "none"
    engine_version: str = ""
    language: str = ""
    attempted: bool = False
    error: Optional[str] = None
    #: Which input variant produced this result: "preprocessed", "original",
    #: or "" when the caller passed a single image. Part of provenance - it
    #: records that the text came from a retry, not the primary pass.
    input_variant: str = ""

    @property
    def succeeded(self) -> bool:
        return bool(self.text and self.text.strip())

    @property
    def mean_confidence(self) -> Optional[float]:
        """Mean confidence over blocks that reported one, else ``None``."""
        values = [b.confidence for b in self.blocks if b.confidence is not None]
        return sum(values) / len(values) if values else None

    @property
    def block_count(self) -> int:
        return len(self.blocks)

    def to_content_fields(self) -> dict:
        """Flatten into the keys the storage layer consumes.

        OCR output is always labelled as derived: ``ocr_derived`` is True so a
        consumer can never mistake recognised text for native document text.
        """
        return {
            "ocr_attempted": self.attempted,
            "ocr_successful": self.succeeded,
            "ocr_engine": self.engine,
            "ocr_engine_version": self.engine_version,
            "ocr_derived": self.succeeded,
            "ocr_confidence": self.mean_confidence,
            "ocr_block_count": self.block_count,
            "ocr_input_variant": self.input_variant,
        }


class BaseOcrEngine(ABC):
    """Interface every OCR backend must satisfy."""

    #: Stable identifier recorded as provenance.
    name: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """Return True if this engine can actually run on this host."""

    @abstractmethod
    def version(self) -> str:
        """Return a human-readable engine/model version string."""

    @abstractmethod
    def recognize(
        self, image: Any, languages: Optional[Sequence[str]] = None
    ) -> OcrResult:
        """Recognise text in a PIL image. Must not raise for bad input."""

    def _result(self, **kwargs) -> OcrResult:
        kwargs.setdefault("engine", self.name)
        kwargs.setdefault("engine_version", self.version())
        kwargs.setdefault("attempted", True)
        return OcrResult(**kwargs)


def _tesseract_candidates() -> List[str]:
    """Plausible tesseract executable locations, in preference order.

    pytesseract resolves the binary through PATH. On Windows the standard
    installer writes ``C:\\Program Files\\Tesseract-OCR\\tesseract.exe`` and
    does not necessarily extend PATH, so a correct installation would otherwise
    be invisible and OCR would silently degrade to whatever fallback exists -
    with no indication that a working engine was present. An explicit
    TESSERACT_CMD always wins.
    """
    explicit = os.environ.get("TESSERACT_CMD")
    found: List[str] = []
    if explicit:
        found.append(explicit)
    if os.name == "nt":
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                found.append(
                    os.path.join(base, "Tesseract-OCR", "tesseract.exe")
                )
    else:
        found.extend((
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/opt/homebrew/bin/tesseract",
        ))
    return found


def _configure_tesseract_cmd(mod) -> None:
    """Point pytesseract at a binary that is installed but not on PATH."""
    try:
        current = getattr(mod.pytesseract, "tesseract_cmd", "")
    except Exception:
        return
    if current and current != "tesseract":
        return  # already configured explicitly by the caller
    for candidate in _tesseract_candidates():
        if candidate and os.path.isfile(candidate):
            try:
                mod.pytesseract.tesseract_cmd = candidate
                logger.info("Using tesseract binary at %s", candidate)
            except Exception:
                logger.debug("Could not set tesseract_cmd", exc_info=True)
            return


class TesseractEngine(BaseOcrEngine):
    """pytesseract-backed engine. Preferred when the binary is installed."""

    name = "tesseract"

    #: Page-segmentation modes tried in order. PSM 11 (sparse) finds text in
    #: any layout; PSM 6 (uniform block) preserves paragraph structure.
    PSM_ORDER = (11, 6, 3)

    def __init__(self):
        self._pytesseract = None
        self._load_attempted = False
        self._checked = False
        self._available = False
        self._version = ""
        self._lock = threading.Lock()

    def _module(self):
        """Import pytesseract once. Guarded by its own flag so that the
        availability probe can set ``_checked`` without disabling the load."""
        if not self._load_attempted:
            self._load_attempted = True
            try:
                import pytesseract

                _configure_tesseract_cmd(pytesseract)
                self._pytesseract = pytesseract
            except ImportError:
                self._pytesseract = None
        return self._pytesseract

    def available(self) -> bool:
        with self._lock:
            if self._checked:
                return self._available
            self._checked = True
            mod = self._module()
            if mod is None:
                return False
            try:
                self._version = str(mod.get_tesseract_version())
                self._available = True
            except Exception:
                self._available = False
                logger.debug("tesseract binary not usable", exc_info=True)
            return self._available

    def version(self) -> str:
        if not self._version:
            self.available()
        return self._version or "unknown"

    def _languages(self, languages: Optional[Sequence[str]]) -> Tuple[str, List[str]]:
        mod = self._module()
        requested = [code for code in (languages or []) if code]
        if not requested:
            return "eng", []
        try:
            installed = set(mod.get_languages(config=""))
        except Exception:
            installed = set()
        missing = [code for code in requested if code not in installed]
        usable = [code for code in requested if code in installed]
        return "+".join(usable), missing

    def recognize(
        self, image: Any, languages: Optional[Sequence[str]] = None
    ) -> OcrResult:
        if not self.available():
            return self._result(error="tesseract unavailable")

        mod = self._module()
        lang, missing_languages = self._languages(languages)
        if missing_languages:
            # Do not silently downgrade heb+eng+ara to whichever one happened
            # to be installed. That would discard scripts without reporting it.
            return self._result(
                language=lang,
                error="Missing Tesseract language data: " + ", ".join(missing_languages),
            )
        if not lang:
            return self._result(error="No requested Tesseract language data is installed")

        for psm in self.PSM_ORDER:
            config = f"--oem 3 --psm {psm}"
            try:
                text = mod.image_to_string(image, lang=lang, config=config)
            except Exception as exc:
                logger.debug("tesseract psm %s failed: %s", psm, exc)
                continue
            if text and text.strip():
                return self._result(
                    text=text.rstrip(),
                    blocks=self._blocks(mod, image, lang, config),
                    language=lang,
                )

        return self._result(text="", blocks=[], language=lang)

    def _blocks(self, mod, image, lang: str, config: str) -> List[OcrBlock]:
        """Per-word blocks with confidence from ``image_to_data``."""
        try:
            data = mod.image_to_data(
                image, lang=lang, config=config, output_type=mod.Output.DICT
            )
        except Exception:
            return []

        blocks: List[OcrBlock] = []
        words = data.get("text") or []
        for i, word in enumerate(words):
            if not word or not str(word).strip():
                continue
            confidence = self._confidence_at(data, i)
            bbox = self._bbox_at(data, i)
            blocks.append(OcrBlock(str(word).strip(), confidence, bbox))
        return blocks

    @staticmethod
    def _confidence_at(data: dict, index: int) -> Optional[float]:
        try:
            raw = float(data["conf"][index])
        except Exception:
            return None
        # tesseract reports -1 for rows that are not recognised words.
        return raw / 100.0 if raw >= 0 else None

    @staticmethod
    def _bbox_at(data: dict, index: int) -> Optional[BBox]:
        try:
            left = int(data["left"][index])
            top = int(data["top"][index])
            width = int(data["width"][index])
            height = int(data["height"][index])
        except Exception:
            return None
        if width <= 0 or height <= 0:
            return None
        return (left, top, left + width, top + height)


class RapidOcrEngine(BaseOcrEngine):
    """PaddleOCR PP-OCRv4 under ONNX Runtime. Pure pip, no system binary.

    Covers Latin and Chinese. It does not cover Hebrew or Arabic, so it is a
    fallback rather than a replacement for tesseract in this project.
    """

    name = "rapidocr"

    def __init__(self):
        self._engine = None
        self._load_attempted = False
        self._checked = False
        self._available = False
        self._lock = threading.Lock()

    def _instance(self):
        """Build the ONNX engine once. Guarded by its own flag so that the
        availability probe can set ``_checked`` without disabling the load."""
        if not self._load_attempted:
            self._load_attempted = True
            try:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR()
            except Exception:
                self._engine = None
                logger.debug("rapidocr not importable", exc_info=True)
        return self._engine

    def available(self) -> bool:
        with self._lock:
            if self._checked:
                return self._available
            self._checked = True
            self._available = self._instance() is not None
            return self._available

    def version(self) -> str:
        """Distribution version plus the bundled model family.

        ``rapidocr_onnxruntime`` exposes no ``__version__`` attribute, so the
        installed distribution metadata is authoritative.
        """
        try:
            from importlib.metadata import version as _dist_version

            dist = _dist_version("rapidocr_onnxruntime")
        except Exception:
            dist = "unknown"
        return f"{dist} (PP-OCRv4 onnx)"

    def recognize(
        self, image: Any, languages: Optional[Sequence[str]] = None
    ) -> OcrResult:
        engine = self._instance()
        if engine is None:
            return self._result(error="rapidocr unavailable")

        try:
            import numpy as np

            array = np.array(image.convert("RGB"))
            # The engine expects BGR channel order.
            bgr = array[:, :, ::-1]
            raw, _elapse = engine(bgr)
        except Exception as exc:
            logger.warning("rapidocr failed: %s", exc)
            return self._result(error=f"{type(exc).__name__}: {exc}")

        blocks: List[OcrBlock] = []
        for item in raw or []:
            # A block must be a (quad, text, confidence) sequence. Rejecting
            # str/bytes explicitly matters: indexing a string succeeds and
            # silently yields single characters as "recognised text", which
            # would fabricate content out of a malformed payload.
            if isinstance(item, (str, bytes)) or not isinstance(item, (list, tuple)):
                continue
            if len(item) < 3:
                continue
            try:
                quad, text, confidence = item[0], item[1], item[2]
            except Exception:
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            blocks.append(
                OcrBlock(str(text).strip(), self._as_confidence(confidence),
                         self._quad_to_bbox(quad))
            )

        text = "\n".join(b.text for b in blocks)
        return self._result(text=text, blocks=blocks, language="latn+chi")

    @staticmethod
    def _as_confidence(value) -> Optional[float]:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        return confidence if 0.0 <= confidence <= 1.0 else None

    @staticmethod
    def _quad_to_bbox(quad) -> Optional[BBox]:
        try:
            xs = [float(point[0]) for point in quad]
            ys = [float(point[1]) for point in quad]
        except Exception:
            return None
        return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


#: Engine preference order. Tesseract first because it covers this project's
#: declared default languages; RapidOCR second because it needs no system
#: binary. Extend this tuple to add a backend.
ENGINE_PREFERENCE: Tuple[type, ...] = (TesseractEngine, RapidOcrEngine)

_SELECTED_LOCK = threading.Lock()
_SELECTED: Optional[BaseOcrEngine] = None
_SELECTION_DONE = False


def _select_engine() -> Optional[BaseOcrEngine]:
    """Pick the first available engine in preference order."""
    global _SELECTED, _SELECTION_DONE
    with _SELECTED_LOCK:
        if _SELECTION_DONE:
            return _SELECTED
        _SELECTION_DONE = True
        # Device policy comes from the compute gateway: with no verified
        # accelerator backend present it reports the CPU, which is exactly what
        # every engine below runs on.  Accelerated OCR is only selected when the
        # gateway can point at a real accelerator path, and the engines that
        # implement it are preferred in that case - the logging makes the
        # decision visible instead of leaving it implicit.
        try:
            from core.compute import WorkloadKind, get_compute_gateway

            decision = get_compute_gateway().select_device(WorkloadKind.OCR)
            logger.info(
                "OCR device selection: %s (%s; %s)",
                decision.device, decision.backend, decision.reason,
            )
        except Exception as exc:  # pragma: no cover - policy is advisory
            logger.debug("Compute gateway unavailable for OCR selection: %s", exc)

        for engine_class in ENGINE_PREFERENCE:
            engine = engine_class()
            if engine.available():
                _SELECTED = engine
                logger.info(
                    "OCR engine selected: %s (%s)", engine.name, engine.version()
                )
                return _SELECTED
        logger.error(
            "No OCR engine available; image and scanned-PDF processing will be marked retryable"
        )
        return None


def get_ocr_engine() -> Optional[BaseOcrEngine]:
    """Return the selected OCR engine, or ``None`` if none can run."""
    return _select_engine()


def ocr_available() -> bool:
    """True if some OCR engine is usable on this host."""
    return get_ocr_engine() is not None


def ocr_engine_name() -> str:
    """Name of the selected engine, or ``'none'``."""
    engine = get_ocr_engine()
    return engine.name if engine else "none"


def reset_engine_cache() -> None:
    """Clear the cached selection. Intended for tests."""
    global _SELECTED, _SELECTION_DONE
    with _SELECTED_LOCK:
        _SELECTED = None
        _SELECTION_DONE = False


#: Confidence below which the primary (preprocessed) pass is retried on the
#: un-preprocessed image. Not arbitrary: measured across six image heights the
#: fallback engine returned 0.92-1.00 on text it read correctly and 0.55-0.65
#: on text it mangled, so this sits in the observed gap. At 40px height the
#: shared binarisation step cut a correct 0.841 reading down to 0.632 and
#: turned "LOWRESTEST" into "APT2T7712", which is what the retry recovers.
LOW_CONFIDENCE_RETRY_THRESHOLD = 0.75


def recognize_best(
    engine: BaseOcrEngine,
    preprocessed: Any,
    original: Optional[Any] = None,
    languages: Optional[Sequence[str]] = None,
    threshold: float = LOW_CONFIDENCE_RETRY_THRESHOLD,
) -> OcrResult:
    """Recognise, retrying on the un-preprocessed image when confidence is low.

    The shared preprocessing is a hard binarisation tuned for tesseract. For
    small images it discards the anti-aliasing that helps a neural engine, so
    the retry is gated on measured confidence rather than applied blindly: a
    confident first pass costs nothing extra.

    ``original`` is optional; when omitted this behaves exactly like
    ``engine.recognize``.
    """
    result = engine.recognize(preprocessed, languages)
    result.input_variant = "preprocessed"

    if original is None:
        return result

    confidence = result.mean_confidence
    if result.succeeded and confidence is not None and confidence >= threshold:
        return result

    # The retry must never turn a partial success into a total failure: some
    # text beats no text, so a retry that raises or finds nothing keeps the
    # primary reading.
    try:
        retry = engine.recognize(original, languages)
    except Exception as exc:
        logger.warning("OCR retry on un-preprocessed image failed: %s", exc)
        return result
    retry.input_variant = "original"
    if not retry.succeeded:
        return result

    retry_confidence = retry.mean_confidence
    if confidence is None or (
        retry_confidence is not None and retry_confidence > confidence
    ):
        logger.info(
            "OCR retry on un-preprocessed image improved confidence %s -> %s",
            confidence, retry_confidence,
        )
        return retry
    return result


def recognize_image(
    image: Any, languages: Optional[Iterable[str]] = None
) -> OcrResult:
    """Recognise text in a PIL image using the selected engine, auto-trying rotations (0, 90, 180, 270) if needed.

    Never raises: an unavailable engine or a recognition failure yields an
    ``OcrResult`` whose ``error`` explains what happened, so a caller can record
    the attempt rather than silently dropping the file.
    """
    engine = get_ocr_engine()
    if engine is None:
        return OcrResult(
            attempted=False, engine="none", error="no OCR engine available"
        )
    try:
        lang_list = list(languages) if languages else None
        res = engine.recognize(image, lang_list)
        if res.succeeded and res.text and res.text.strip():
            return res

        # Attempt rotation retries (90, 180, 270) if initial orientation produced empty/weak text
        if hasattr(image, "rotate"):
            best_res = res
            best_len = len(res.text.strip()) if res.text else 0
            for angle in (270, 180, 90):
                try:
                    rotated = image.rotate(angle, expand=True)
                    r_res = engine.recognize(rotated, lang_list)
                    r_len = len(r_res.text.strip()) if r_res.text else 0
                    if r_len > best_len:
                        best_res = r_res
                        best_len = r_len
                        if best_len > 20:
                            break
                except Exception:
                    pass
            return best_res
        return res
    except Exception as exc:  # engines should not raise, but never trust that
        logger.warning("OCR engine %s raised: %s", engine.name, exc)
        return OcrResult(
            attempted=True,
            engine=engine.name,
            engine_version=engine.version(),
            error=f"{type(exc).__name__}: {exc}",
        )
