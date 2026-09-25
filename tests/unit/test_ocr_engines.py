"""OCR engine layer and image-reader integration (PHASE 2A).

Before this work the reader hard-wired a single backend:

    if pytesseract and self._is_tesseract_available():

so on a host without the tesseract binary no OCR happened at all and scanned
documents produced nothing - the exact gap the Phase 1 audit recorded as its
largest evidence hole.

These tests exercise the engine abstraction with stubs (so the tesseract code
path is covered even where the binary is absent) and the real reader against
real images using the engine that is actually installed here.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.ocr.engines as ocr_engines  # noqa: E402
from core.ocr import (  # noqa: E402
    OcrBlock,
    OcrResult,
    RapidOcrEngine,
    TesseractEngine,
    ocr_available,
    ocr_engine_name,
    recognize_image,
    reset_engine_cache,
)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@pytest.fixture(autouse=True)
def _fresh_engine_cache():
    reset_engine_cache()
    yield
    reset_engine_cache()


def make_text_image(tmp_path, text, size=44, canvas=(1100, 260), name="scan.png"):
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    img = Image.new("RGB", canvas, "white")
    ImageDraw.Draw(img).text((40, 90), text, font=ImageFont.truetype(FONT, size),
                             fill="black")
    path = tmp_path / name
    img.save(path)
    return path


# ----------------------------------------------------------------------
# OcrResult / OcrBlock value semantics
# ----------------------------------------------------------------------
class TestOcrResult:
    def test_empty_result_did_not_succeed(self):
        assert OcrResult(attempted=True).succeeded is False

    def test_whitespace_only_did_not_succeed(self):
        assert OcrResult(text="   \n ", attempted=True).succeeded is False

    def test_text_succeeds(self):
        assert OcrResult(text="real words", attempted=True).succeeded is True

    def test_mean_confidence_ignores_unknown(self):
        """A block that reports no confidence must not drag the mean to zero."""
        result = OcrResult(
            blocks=[OcrBlock("a", 0.8), OcrBlock("b", None), OcrBlock("c", 0.6)]
        )
        assert result.mean_confidence == pytest.approx(0.7)

    def test_mean_confidence_none_when_nothing_reported(self):
        assert OcrResult(blocks=[OcrBlock("a", None)]).mean_confidence is None

    def test_unknown_confidence_is_not_zero(self):
        """'unknown' must stay distinguishable from 'certainly wrong'."""
        assert OcrBlock("a", None).confidence is None
        assert OcrBlock("b", 0.0).confidence == 0.0

    def test_to_content_fields_marks_text_as_derived(self):
        """OCR output must never be presented as native document text."""
        fields = OcrResult(text="hi", blocks=[OcrBlock("hi", 0.9)],
                           engine="rapidocr", engine_version="1.4.4",
                           attempted=True).to_content_fields()
        assert fields["ocr_derived"] is True
        assert fields["ocr_successful"] is True
        assert fields["ocr_engine"] == "rapidocr"
        assert fields["ocr_confidence"] == pytest.approx(0.9)

    def test_to_content_fields_not_derived_when_empty(self):
        fields = OcrResult(text="", attempted=True).to_content_fields()
        assert fields["ocr_derived"] is False
        assert fields["ocr_successful"] is False


# ----------------------------------------------------------------------
# Tesseract engine, driven by a stub so the path is covered without the binary
# ----------------------------------------------------------------------
class _StubOutput:
    DICT = "dict"


class _StubTesseract:
    """Records calls and replays canned text/data per PSM mode."""

    Output = _StubOutput

    def __init__(self, text_by_psm=None, data=None, languages=("eng", "heb", "ara"),
                 version="5.3.0"):
        self.text_by_psm = text_by_psm or {}
        self.data = data
        self.languages = list(languages)
        self._version = version
        self.calls = []

    def get_tesseract_version(self):
        return self._version

    def get_languages(self, config=""):
        return self.languages

    def image_to_string(self, image, lang=None, config=None):
        self.calls.append(("string", lang, config))
        psm = int(config.split("--psm")[1].strip()) if config and "--psm" in config else 3
        return self.text_by_psm.get(psm, "")

    def image_to_data(self, image, lang=None, config=None, output_type=None):
        self.calls.append(("data", lang, config))
        return self.data or {
            "text": ["", "SCAN", "MARKER"],
            "conf": ["-1", "92", "-1"],
            "left": [0, 10, 0], "top": [0, 20, 0],
            "width": [0, 100, 0], "height": [0, 30, 0],
        }


def tesseract_with_stub(stub):
    engine = TesseractEngine()
    engine._pytesseract = stub
    engine._load_attempted = True
    engine._checked = True
    engine._available = True
    engine._version = stub._version
    return engine


class TestTesseractEngine:
    def test_uses_first_psm_mode_that_yields_text(self):
        stub = _StubTesseract(text_by_psm={11: "", 6: "from psm six"})
        result = tesseract_with_stub(stub).recognize(object())
        assert result.text == "from psm six"
        assert result.engine == "tesseract"
        assert result.engine_version == "5.3.0"

    def test_confidence_parsed_from_image_to_data(self):
        stub = _StubTesseract(text_by_psm={11: "SCAN MARKER"})
        result = tesseract_with_stub(stub).recognize(object())
        # "MARKER" has conf -1 -> unknown, must not become 0.0
        confidences = [b.confidence for b in result.blocks]
        assert 0.92 in confidences
        assert None in confidences

    def test_word_with_negative_confidence_has_no_bbox_for_zero_size(self):
        stub = _StubTesseract(text_by_psm={11: "SCAN MARKER"})
        result = tesseract_with_stub(stub).recognize(object())
        bboxes = [b.bbox for b in result.blocks]
        assert (10, 20, 110, 50) in bboxes
        assert None in bboxes  # zero-width rows are not real boxes

    def test_requested_languages_filtered_to_installed(self):
        stub = _StubTesseract(text_by_psm={11: "x"}, languages=("eng", "ara"))
        result = tesseract_with_stub(stub).recognize(object(), ["heb", "ara", "zzz"])
        assert result.language == "ara"

    def test_missing_requested_language_is_not_silently_replaced(self):
        """A missing script is reported, never hidden by English fallback."""
        stub = _StubTesseract(text_by_psm={11: "x"}, languages=("deu",))
        result = tesseract_with_stub(stub).recognize(object(), ["heb"])
        assert result.language == ""
        assert result.text == ""
        assert result.error == "Missing Tesseract language data: heb"
        assert result.attempted is True

    def test_no_text_in_any_psm_is_a_clean_empty_result(self):
        stub = _StubTesseract(text_by_psm={})
        result = tesseract_with_stub(stub).recognize(object())
        assert result.succeeded is False
        assert result.attempted is True
        assert result.error is None

    def test_exception_in_one_psm_continues_to_the_next(self):
        class Exploding(_StubTesseract):
            def image_to_string(self, image, lang=None, config=None):
                if "--psm 11" in (config or ""):
                    raise RuntimeError("boom")
                return super().image_to_string(image, lang, config)

        result = tesseract_with_stub(Exploding(text_by_psm={6: "recovered"})).recognize(object())
        assert result.text == "recovered"

    def test_unavailable_engine_reports_error_not_exception(self):
        engine = TesseractEngine()
        engine._load_attempted = True
        engine._pytesseract = None
        engine._checked = True
        engine._available = False
        result = engine.recognize(object())
        assert result.attempted is True
        assert "unavailable" in (result.error or "")


# ----------------------------------------------------------------------
# RapidOCR engine, driven by a stub
# ----------------------------------------------------------------------
class _StubRapid:
    def __init__(self, payload):
        self.payload = payload

    def __call__(self, image):
        return self.payload, None


def rapid_with_stub(payload):
    engine = RapidOcrEngine()
    engine._engine = _StubRapid(payload)
    engine._load_attempted = True
    engine._checked = True
    engine._available = True
    return engine


def quad(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class TestRapidOcrEngine:
    def test_blocks_text_confidence_and_bbox(self):
        payload = [[quad(10, 20, 110, 50), "INVOICE 4471", 0.97]]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.text == "INVOICE 4471"
        assert result.blocks[0].confidence == pytest.approx(0.97)
        assert result.blocks[0].bbox == (10, 20, 110, 50)
        assert result.mean_confidence == pytest.approx(0.97)

    def test_multiple_blocks_join_with_newlines(self):
        payload = [
            [quad(0, 0, 10, 10), "line one", 0.9],
            [quad(0, 20, 10, 30), "line two", 0.8],
        ]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.text == "line one\nline two"
        assert result.block_count == 2

    def test_empty_payload_is_a_clean_empty_result(self):
        result = rapid_with_stub([]).recognize(_pil_white())
        assert result.succeeded is False
        assert result.error is None

    def test_none_payload_is_a_clean_empty_result(self):
        result = rapid_with_stub(None).recognize(_pil_white())
        assert result.succeeded is False

    def test_blank_text_blocks_are_dropped(self):
        payload = [[quad(0, 0, 10, 10), "   ", 0.9], [quad(0, 20, 10, 30), "real", 0.9]]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.block_count == 1

    def test_out_of_range_confidence_becomes_unknown(self):
        payload = [[quad(0, 0, 10, 10), "x", 5.0]]
        assert rapid_with_stub(payload).recognize(_pil_white()).blocks[0].confidence is None

    def test_malformed_block_is_skipped_not_fatal(self):
        payload = ["not-a-block", [quad(0, 0, 10, 10), "good", 0.9]]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.text == "good"

    def test_string_payload_never_fabricates_characters(self):
        """Regression: indexing a str yields chars, which read as OCR text.

        'not-a-block'[1] == 'o' previously surfaced as recognised text, so a
        malformed engine payload invented content.
        """
        payload = ["not-a-block", b"binary-blob"]
        result = rapid_with_stub(payload).recognize(_pil_white())
        assert result.text == ""
        assert result.block_count == 0

    def test_non_string_text_is_rejected(self):
        payload = [[quad(0, 0, 10, 10), 12345, 0.9]]
        assert rapid_with_stub(payload).recognize(_pil_white()).block_count == 0

    def test_short_block_is_rejected(self):
        payload = [[quad(0, 0, 10, 10), "x"]]
        assert rapid_with_stub(payload).recognize(_pil_white()).block_count == 0

    def test_engine_failure_is_reported_not_raised(self):
        class Boom:
            def __call__(self, image):
                raise RuntimeError("model exploded")

        engine = RapidOcrEngine()
        engine._engine = Boom()
        engine._load_attempted = True
        engine._checked = True
        engine._available = True
        result = engine.recognize(_pil_white())
        assert result.succeeded is False
        assert "model exploded" in (result.error or "")


def _pil_white():
    Image = pytest.importorskip("PIL.Image")
    return Image.new("RGB", (120, 60), "white")


# ----------------------------------------------------------------------
# Engine selection
# ----------------------------------------------------------------------
class TestEngineSelection:
    def test_tesseract_is_preferred_when_available(self, monkeypatch):
        """Tesseract covers this project's declared heb/eng/ara defaults."""
        monkeypatch.setattr(TesseractEngine, "available", lambda self: True)
        monkeypatch.setattr(TesseractEngine, "version", lambda self: "5.3.0")
        reset_engine_cache()
        assert ocr_engine_name() == "tesseract"

    def test_falls_back_when_tesseract_unavailable(self, monkeypatch):
        monkeypatch.setattr(TesseractEngine, "available", lambda self: False)
        monkeypatch.setattr(RapidOcrEngine, "available", lambda self: True)
        reset_engine_cache()
        assert ocr_engine_name() == "rapidocr"

    def test_no_engine_is_reported_cleanly(self, monkeypatch):
        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", ())
        reset_engine_cache()
        assert ocr_available() is False
        assert ocr_engine_name() == "none"

    def test_recognize_image_without_engine_returns_error_result(self, monkeypatch):
        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", ())
        reset_engine_cache()
        result = recognize_image(_pil_white())
        assert result.attempted is False
        assert result.engine == "none"
        assert "no OCR engine" in (result.error or "")

    def test_recognize_image_swallows_engine_exceptions(self, monkeypatch):
        class Bad:
            name = "bad"

            def available(self):
                return True

            def version(self):
                return "0"

            def recognize(self, image, languages=None):
                raise ValueError("unexpected")

        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", (Bad,))
        reset_engine_cache()
        result = recognize_image(_pil_white())
        assert result.attempted is True
        assert "unexpected" in (result.error or "")

    def test_preference_order_is_declared_not_incidental(self):
        names = [c.name for c in ocr_engines.ENGINE_PREFERENCE]
        assert names[0] == "tesseract"
        assert "rapidocr" in names


# ----------------------------------------------------------------------
# Image reader integration (real engine, real images)
# ----------------------------------------------------------------------
class TestImageReaderOcr:
    @pytest.fixture
    def reader(self):
        from reader_file.readers.read_img_fast import ImageFileReader

        return ImageFileReader()

    def test_printed_text_is_extracted_with_provenance(self, reader, tmp_path):
        path = make_text_image(tmp_path, "SCAN PROVENANCE MARKER 5521")
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})

        assert out["ocr_attempted"] is True
        assert out["ocr_successful"] is True
        assert "SCAN PROVENANCE MARKER 5521" in out["text"]
        # Provenance: engine, version, derived flag, confidence.
        assert out["ocr_engine"] in ("tesseract", "rapidocr")
        assert out["ocr_engine_version"]
        assert out["ocr_derived"] is True
        assert out["ocr_confidence"] is not None
        assert 0.0 <= out["ocr_confidence"] <= 1.0

    def test_blocks_carry_confidence_and_bbox(self, reader, tmp_path):
        path = make_text_image(tmp_path, "BLOCK COORDINATES 7781")
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        blocks = out.get("ocr_coordinates") or []
        assert blocks, "expected per-block results"
        first = blocks[0]
        assert first["confidence"] is not None
        assert len(first["bbox"]) == 4

    def test_blank_image_yields_no_text_and_no_hallucination(self, reader, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "blank.png"
        Image.new("RGB", (800, 400), "white").save(path)
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert out["ocr_attempted"] is True
        assert out["ocr_successful"] is False
        assert out["text"] == ""
        assert out["extraction_info"]["reason"] == "no_text_extracted"

    def test_tiny_image_is_skipped_without_ocr(self, reader, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "tiny.png"
        Image.new("RGB", (10, 10), "white").save(path)
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert out["ocr_attempted"] is False
        assert out["extraction_info"]["skipped"] is True
        assert out["extraction_info"]["skip_reason"] == "too_small"

    def test_corrupt_image_is_recorded_not_raised(self, reader, tmp_path):
        path = tmp_path / "broken.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage-not-a-real-png" * 20)
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert isinstance(out, dict)
        assert out.get("ocr_successful") is not True

    def test_missing_file_is_recorded(self, reader, tmp_path):
        """`read_file` reports the base-reader error shape for an absent file."""
        absent = tmp_path / "absent.png"
        out = reader.read_file({"path": str(absent), "effective_extension": ".png"})
        assert out["error"].startswith("File not found")
        assert out["path"] == str(absent)

    def test_missing_file_via_image_entry_point_keeps_status_fields(self, reader, tmp_path):
        """`read_image_file_fast` documents the richer status shape."""
        out = reader.read_image_file_fast(str(tmp_path / "absent.png"))
        assert out["ocr_attempted"] is False
        assert out["ocr_successful"] is False
        assert out["extraction_info"]["error"] == "File not found"

    def test_ocr_never_modifies_the_source_file(self, reader, tmp_path):
        """Hard requirement: OCR must not overwrite original data."""
        from core.hashing import hash_file

        path = make_text_image(tmp_path, "IMMUTABLE SOURCE 3312")
        before = hash_file(str(path))
        mtime_before = path.stat().st_mtime
        reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert hash_file(str(path)) == before
        assert path.stat().st_mtime == mtime_before

    def test_no_engine_available_records_the_reason(self, reader, tmp_path, monkeypatch):
        monkeypatch.setattr(ocr_engines, "ENGINE_PREFERENCE", ())
        reset_engine_cache()
        import reader_file.readers.read_img_fast as rif

        monkeypatch.setattr(rif.ImageFileReader, "_is_tesseract_available",
                            lambda self: False)
        path = make_text_image(tmp_path, "NO ENGINE HERE 9902")
        out = reader.read_file({"path": str(path), "effective_extension": ".png"})
        assert out["ocr_attempted"] is False
        assert out["ocr_engine"] == "none"
        assert out["extraction_info"]["error"] == "No OCR engine available"
