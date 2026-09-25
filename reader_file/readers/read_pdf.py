"""
Optimized PDF Reader - 10x Faster Performance
Aligned with database design principles - all functions within class
Improvements:
1. Parallel page processing (multiple pages at once)
2. Smart OCR skipping (detect text-based PDFs early)
3. Reuses OCR functions from read_img_fast.py (no duplication)
4. Adaptive strategy based on PDF characteristics
5. Memory-efficient streaming
6. Early exit on text detection
"""

import os
import io
from pathlib import Path
from typing import Dict, Any, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
import logging
import time

# Suppress PIL warning about palette images with transparency
# We handle this by converting to RGBA when needed

# Import shared OCR functions from read_img_fast (avoid duplication)
# These are module-level functions exported from ImageFileReader for cross-module use
from reader_file.readers.read_img_fast import (
    _get_libraries,
    _detect_language,
    _get_optimized_tesseract_config,
    _fast_preprocess,
    _is_tesseract_available,
    DEFAULT_OCR_LANGUAGES
)

# PHASE 2A: OCR engine selection. Imported from its owning module rather than
# re-exported through read_img_fast.
from core.ocr import get_ocr_engine, recognize_best

from .base_reader import BaseReader

from Hdg_Err_Ex_Log import (
    handle_error,
    ErrorCategory,
    ErrorSeverity,
    format_validation_error
)

logger = logging.getLogger(__name__)


class PDFFileReader(BaseReader):
    """
    Reader for PDF files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported PDF extensions"""
        return {'.pdf'}
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read PDF file and extract content with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with PDF content or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        # DETECT-01: dispatch on the content-verified type, not the filename.
        ext = self.effective_extension(file_info)
        
        try:
            if ext == '.pdf':
                return self.read_pdf_file(file_path)
            else:
                error_msg = f"Unsupported file type: {ext or file_path}"
                return self.handle_read_error(ValueError(error_msg), file_path, "read_file")
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")
    
    def _check_pdf_libraries(self):
        """Check PDF-specific library (fitz/pymupdf) + get shared libs"""
        libs = _get_libraries()  # Get shared libs from read_img_fast
        
        # Ensure we always have a "missing" key so callers can safely check it
        # Without this, accessing libs["missing"] raises KeyError and bubbles up
        # as an error like "'missing'" for every PDF file.
        if "missing" not in libs or libs["missing"] is None:
            libs["missing"] = []
        
        # Add fitz (PDF-specific)
        if 'fitz' not in libs:
            try:
                import fitz
                libs['fitz'] = fitz
            except ImportError:
                libs['fitz'] = None
                # Track that the PDF library is missing
                if 'pymupdf' not in libs.get('missing', []):
                    libs['missing'] = libs.get('missing', []) + ['pymupdf']
        
        return libs
    
    def detect_pdf_type_early(self, doc, sample_pages=3):
        """
        Quickly detect if PDF has extractable text by sampling first few pages
        Returns: "text" if text-based, "image" if image-based
        """
        total_pages = len(doc)
        pages_to_check = min(sample_pages, total_pages)

        # PDF-01: a PDF with no pages has nothing to sample. Dividing by
        # pages_to_check raised ZeroDivisionError, which surfaced as an opaque
        # "division by zero" failure for the whole document.
        if pages_to_check <= 0:
            return "image"

        text_chars = 0

        for page_num in range(pages_to_check):
            page = doc[page_num]
            text = page.get_text("text")
            if text:
                text_chars += len(str(text).strip())

        # If we found substantial text in sample, it's a text-based PDF
        avg_chars_per_page = text_chars / pages_to_check

        if avg_chars_per_page > 50:  # Threshold: 50 chars per page
            return "text"
        else:
            return "image"
    
    def process_page_optimized(self, page_data):
        """
        Optimized page processing - uses shared OCR functions from read_img_fast
        """
        # PDF-02: page_data carries the page's own text layer so a page whose
        # text is too sparse to skip OCR can still fall back to it. Optional
        # for backward compatibility with the previous 6-element tuple. The
        # 8th field (source path) is likewise optional and used only for
        # diagnostics.
        unpacked = tuple(page_data)
        if len(unpacked) >= 8:
            (page_num, page_bytes, tesseract_lang, tesseract_config,
             needs_ocr, ocr_languages, native_text, source_path) = unpacked[:8]
        elif len(unpacked) >= 7:
            (page_num, page_bytes, tesseract_lang, tesseract_config,
             needs_ocr, ocr_languages, native_text) = unpacked[:7]
            source_path = None
        else:
            (page_num, page_bytes, tesseract_lang, tesseract_config,
             needs_ocr, ocr_languages) = unpacked[:6]
            native_text = ""
            source_path = None
        native_text = native_text or ""
        source_label = os.path.basename(source_path) if source_path \
            else f"page {page_num + 1}"
        
        libs = _get_libraries()  # Shared function
        Image = libs.get('Image')
        pytesseract = libs.get('pytesseract')
        cv2 = libs.get('cv2')
        np = libs.get('np')
        
        result = {
            "page_number": page_num + 1,
            "text": "",
            "text_length": 0,
            "method": "unknown"
        }

        def _fallback_to_text_layer(reason):
            """Keep the page's own text layer instead of discarding it."""
            result["ocr_status"] = reason
            stripped = native_text.strip()
            if stripped:
                result["text"] = stripped
                result["text_length"] = len(stripped)
                result["method"] = "text_layer_fallback"
            else:
                result["text"] = ""
                result["text_length"] = 0
                result["method"] = reason
            return result

        if not needs_ocr:
            result["method"] = "skipped_text_based_pdf"
            return result

        # PHASE 2A: tesseract is preferred (it covers this project's declared
        # heb/eng/ara defaults) but it is no longer the only option. A host
        # without the binary still gets OCR through the engine layer instead of
        # silently returning a blank page.
        use_tesseract = bool(pytesseract and _is_tesseract_available())
        if use_tesseract and ocr_languages:
            try:
                installed_languages = set(pytesseract.get_languages(config=""))
                missing_languages = [
                    code for code in ocr_languages if code not in installed_languages
                ]
                if missing_languages:
                    # Route through the strict shared engine so missing script
                    # data is reported, never silently reduced to English.
                    logger.error(
                        "Missing Tesseract language data for %s: %s",
                        source_label, ", ".join(missing_languages),
                    )
                    use_tesseract = False
            except Exception:
                use_tesseract = False
        fallback_engine = None if use_tesseract else get_ocr_engine()
        if not use_tesseract and fallback_engine is None:
            return _fallback_to_text_layer("ocr_required_engine_unavailable")

        try:
            engine_result = None
            pil_image = Image.open(io.BytesIO(page_bytes))
            # Convert palette images with transparency to RGBA to avoid PIL warnings
            if pil_image.mode == 'P' and 'transparency' in pil_image.info:
                pil_image = pil_image.convert('RGBA')
            rgb_image = pil_image.convert("RGB")

            # Use shared preprocessing from read_img_fast (shared by every engine)
            if cv2 and np:
                img_array = np.array(rgb_image)
                processed = _fast_preprocess(img_array, libs)
                pil_processed = Image.fromarray(processed)
            else:
                pil_processed = pil_image.convert("L")

            if use_tesseract:
                # Use shared language detection from read_img_fast
                detected_lang = _detect_language(rgb_image, pytesseract)
                result["detected_language"] = detected_lang

                # Use shared config function from read_img_fast
                if detected_lang:
                    lang_to_use, config_to_use = _get_optimized_tesseract_config(detected_lang, tuple(ocr_languages) if ocr_languages else None)
                else:
                    lang_to_use, config_to_use = tesseract_lang, tesseract_config

                result["ocr_language"] = lang_to_use

                text = pytesseract.image_to_string(pil_processed, lang=lang_to_use, config=config_to_use)
                if not text or len(text.strip()) < 5:
                    for angle in (270, 180, 90):
                        try:
                            rot_target = pil_processed.rotate(angle, expand=True)
                            rot_text = pytesseract.image_to_string(rot_target, lang=lang_to_use, config=config_to_use)
                            if rot_text and len(rot_text.strip()) > len(text or ""):
                                text = rot_text
                                if len(text.strip()) > 20:
                                    break
                        except Exception:
                            pass

                # Handle None and ensure string type
                if text is None:
                    text = ""
                else:
                    text = str(text)

                engine_name = "tesseract"
                try:
                    engine_version = str(pytesseract.get_tesseract_version())
                except Exception:
                    engine_version = "unknown"
                confidence = None
                input_variant = ""
            else:
                # Alternate engine path. Same contract, plus per-page
                # confidence and explicit engine provenance.
                # Confidence-gated retry on the un-preprocessed page image.
                engine_result = recognize_best(
                    fallback_engine,
                    pil_processed,
                    rgb_image,
                    list(ocr_languages) if ocr_languages else None,
                )
                text = engine_result.text or ""
                result["ocr_language"] = engine_result.language
                if engine_result.error:
                    result["engine_error"] = engine_result.error

                engine_name = engine_result.engine
                engine_version = engine_result.engine_version
                confidence = engine_result.mean_confidence
                input_variant = engine_result.input_variant

            if text and len(text.strip()) > 0:
                result["text"] = text.strip()
                result["text_length"] = len(text.strip())
                result["method"] = "ocr_multilang" if use_tesseract else f"ocr_{engine_name}"
                # Provenance: this text was recognised, not authored.
                result["ocr_engine"] = engine_name
                result["ocr_engine_version"] = engine_version
                result["ocr_derived"] = True
                if confidence is not None:
                    result["ocr_confidence"] = confidence
                if input_variant:
                    result["ocr_input_variant"] = input_variant
            else:
                # OCR found nothing (or could not run). Preserve the native
                # layer, while retaining the actual diagnostic for retry.
                reason = "ocr_failed" if (engine_result and engine_result.error) else "ocr_no_text"
                if engine_result and engine_result.error:
                    result["error"] = engine_result.error
                return _fallback_to_text_layer(reason)

        except Exception as e:
            error_str = str(e).lower()
            # Suppress verbose tesseract errors - we already checked availability
            if 'tesseract' in error_str and ('not installed' in error_str or 'not in your path' in error_str):
                reason = "ocr_required_engine_unavailable"
            else:
                reason = "ocr_failed"
                result["error"] = str(e)
            return _fallback_to_text_layer(reason)

        return result
    
    def read_pdf_file(self, filepath, max_workers=None, languages=None):
        """
        Highly optimized PDF reader with parallel processing and multi-language OCR
        
        Args:
            filepath: Path to PDF file
            max_workers: Number of parallel OCR workers (default: auto)
            languages: List of language codes for OCR (default: ["eng", "ara", "heb"])
                       Supports all Tesseract languages installed on system
        
        Features:
        1. Early detection of PDF type (text vs image)
        2. Auto-detection of page language/script
        3. Multi-language OCR (Hebrew, Arabic, English, etc.)
        4. Parallel OCR processing for image-based PDFs
        5. Skip OCR for text-based PDFs entirely
        6. Adaptive worker count based on CPU cores
        """
        
        # Check libraries (uses shared + PDF-specific)
        libs = self._check_pdf_libraries()
        fitz = libs.get('fitz')
        pytesseract = libs.get('pytesseract')

        # Return error if any required libraries are missing
        missing_libs = libs.get('missing') or []
        if missing_libs:
            return {
                "error": f"Missing required libraries: {', '.join(missing_libs)}",
                "install_command": f"pip install {' '.join(missing_libs)}",
                "filepath": str(filepath),
                "pages": [],
                "total_characters": 0
            }
        
        # Determine optimal worker count
        if max_workers is None:
            # Use more workers for I/O-bound OCR tasks
            max_workers = min(multiprocessing.cpu_count() * 2, 8)
        
        filepath = Path(filepath)
        if not filepath.exists():
            return {
                "error": "File not found", 
                "filepath": str(filepath),
                "pages": [],
                "total_characters": 0
            }
        
        start_time = time.time()
        
        try:
            result = {
                "filepath": str(filepath),
                "num_pages": 0,
                "is_encrypted": False,
                "metadata": {},
                "pages": [],
                "ocr_used": False,
                "processing_time": 0
            }
            
            # Open PDF
            doc = fitz.open(filepath)
            result["num_pages"] = len(doc)
            result["is_encrypted"] = doc.is_encrypted
            
            # Handle encrypted PDFs - try to decrypt or return error
            if doc.is_encrypted:
                # Try to decrypt with empty password (many PDFs are encrypted but allow empty password)
                if not doc.authenticate(""):
                    logger.warning(f"PDF is encrypted and cannot be decrypted: {filepath.name}")
                    doc.close()
                    return {
                        "error": "PDF is encrypted and cannot be decrypted",
                        "filepath": str(filepath),
                        "is_encrypted": True,
                        "pages": [],
                        "total_characters": 0
                    }
            
            result["metadata"] = doc.metadata

            # PDF-02: a PDF is a container. Annotations, comments, embedded
            # files, JavaScript, form fields, signature fields and incremental
            # revisions live in it whether or not they are ever rendered on a
            # page, and none of them are visible to a page-text extraction. The
            # feature pass below records them, materialises the embedded files
            # as child objects (so they are hashed, stored and processed like
            # any other artifact) and never lets a failure there lose the page
            # text that was already extracted.
            try:
                from core.forensics.pdf import (
                    extract_pdf_features,
                    materialise_embedded_files,
                    pdf_feature_text,
                )

                pdf_features = extract_pdf_features(filepath, doc=doc)
                # Materialise the attachments first, so the records that reach
                # the result already name the file each one was written to.
                written = materialise_embedded_files(pdf_features, str(filepath), result)
                result["forensic_features"] = pdf_features
                result["annotations"] = pdf_features.get("annotations", [])
                result["embedded_files"] = [
                    {key: value for key, value in record.items() if key != "content"}
                    for record in pdf_features.get("embedded_files", [])
                ]
                result["javascript"] = pdf_features.get("javascript", [])
                result["form_fields"] = pdf_features.get("form_fields", [])
                result["revisions"] = pdf_features.get("revisions", {})
                result["has_signatures"] = pdf_features.get("has_signatures", False)
                if written:
                    logger.info("Materialised %d PDF embedded file(s) for %s",
                                len(written), filepath.name)
                feature_text = pdf_feature_text(pdf_features)
                if feature_text:
                    result["forensic_text"] = feature_text
                if pdf_features.get("extraction_errors"):
                    result["extraction_errors"] = pdf_features["extraction_errors"]
            except Exception as exc:
                # Feature extraction is additive: record the failure and keep
                # the page text rather than failing the artifact.
                result.setdefault("extraction_errors", []).append(
                    {"scope": "pdf_features", "error": str(exc)})
            
            # ===== CRITICAL OPTIMIZATION: Detect PDF type early =====
            logger.info(f"Detecting PDF type for {filepath.name}...")
            pdf_type = self.detect_pdf_type_early(doc, sample_pages=3)
            logger.info(f"PDF type detected: {pdf_type}")
            
            if pdf_type == "text":
                # ===== TEXT-BASED PDF: Direct extraction (VERY FAST) =====
                # PRODUCTION: Streaming extraction for very large PDFs
                num_pages = len(doc)
                file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                is_very_large = file_size > 100 * 1024 * 1024 or num_pages > 1000  # > 100MB or > 1000 pages
                
                if is_very_large:
                    logger.info(f"Large PDF detected ({file_size / (1024*1024):.2f}MB, {num_pages} pages), using streaming extraction")
                
                logger.info(f"Using direct text extraction for {filepath.name}")
                
                try:
                    # PRODUCTION: Process pages incrementally to prevent memory buildup
                    max_text_length = 0
                    total_text_length = 0
                    
                    for page_num in range(num_pages):
                        # Yield periodically to prevent CPU spikes
                        self.yield_periodically(page_num, interval=5, aggressive=False)
                        
                        # PRODUCTION: For very large PDFs, yield more frequently
                        if is_very_large and page_num % 10 == 0:
                            self.yield_cpu_if_needed(aggressive=True)
                        
                        page = doc[page_num]
                        # Get text - handle None and ensure string type
                        text = page.get_text("text")
                        if text is None:
                            text = ""
                        else:
                            text = str(text)
                        
                        text_len = len(text)
                        total_text_length += text_len
                        max_text_length = max(max_text_length, text_len)
                        
                        # PRODUCTION: For very large PDFs, limit stored text per page to prevent memory exhaustion
                        # Store full text for smaller PDFs, but limit for very large ones
                        stored_text = text
                        if is_very_large and text_len > 10 * 1024 * 1024:  # > 10MB per page
                            # Store first 5MB and last 5MB, with indicator
                            stored_text = text[:5 * 1024 * 1024] + "\n\n[... TRUNCATED FOR LARGE FILE ...]\n\n" + text[-5 * 1024 * 1024:]
                            logger.debug(f"Page {page_num + 1} text truncated ({text_len / (1024*1024):.2f}MB -> {len(stored_text) / (1024*1024):.2f}MB)")
                        
                        result["pages"].append({
                            "page_number": page_num + 1,
                            "text": stored_text,
                            "text_length": text_len,  # Store original length
                            "stored_text_length": len(stored_text),  # Store actual stored length
                            "method": "direct_extraction"
                        })
                        
                        # PRODUCTION: Log progress for very large PDFs
                        if is_very_large and (page_num + 1) % 100 == 0:
                            progress_pct = ((page_num + 1) / num_pages) * 100
                            logger.info(f"Extracted {page_num + 1}/{num_pages} pages ({progress_pct:.1f}%) - {total_text_length / (1024*1024):.2f}MB total text")
                    
                    # Store summary statistics
                    result["total_text_length"] = total_text_length
                    result["max_page_text_length"] = max_text_length
                    result["is_very_large"] = is_very_large
                    
                finally:
                    # Always close the document to prevent resource leaks
                    doc.close()
                
                result["ocr_used"] = False
                
            else:
                # ===== IMAGE-BASED PDF: OCR required (SLOWER) =====
                logger.info(f"Using parallel OCR for {filepath.name} ({len(doc)} pages, {max_workers} workers)")
                result["ocr_used"] = True
                
                # Get Tesseract config with multi-language support (shared function)
                ocr_languages = languages if languages else DEFAULT_OCR_LANGUAGES
                tesseract_lang, tesseract_config = _get_optimized_tesseract_config(None, tuple(ocr_languages))
                result["ocr_languages"] = ocr_languages

                # PDF-02: if no OCR engine is present, rasterizing every page is
                # pure waste and previously ended with the text layer thrown
                # away. Decide once, up front. PHASE 2A: "available" now means
                # any engine, not specifically the tesseract binary.
                ocr_available = (bool(pytesseract) and _is_tesseract_available()) \
                    or get_ocr_engine() is not None
                if not ocr_available:
                    # The missing backend is a visible caveat, recorded on the
                    # document (and per page) as ``ocr_status``. It must NOT
                    # fail the document up front: whether any content was
                    # actually lost is decided after the page loop, from the
                    # pages that could not be read at all (see the
                    # ``unavailable_pages`` block below). A PDF whose pages all
                    # carry native text is a completed extraction whether or
                    # not an OCR backend exists.
                    result["ocr_status"] = "ocr_required_engine_unavailable"
                    logger.error(
                        "OCR required but no engine is available for %s; native text layers will be preserved",
                        filepath.name,
                    )
                
                # Prepare page data for parallel processing
                page_data_list = []
                
                try:
                    for page_num in range(len(doc)):
                        # Yield periodically during preparation
                        self.yield_periodically(page_num, interval=10, aggressive=False)
                        
                        try:
                            page = doc[page_num]
                            
                            # Quick check: does this page have text?
                            text = page.get_text("text")
                            # Handle None and ensure string type
                            if text is None:
                                text = ""
                            else:
                                text = str(text)
                            
                            if len(text.strip()) > 30:
                                # Page has text, no OCR needed
                                result["pages"].append({
                                    "page_number": page_num + 1,
                                    "text": text,
                                    "text_length": len(text),
                                    "method": "direct_extraction"
                                })
                            elif not ocr_available:
                                # Do not silently classify a scanned page as
                                # processed. Preserve any native text layer,
                                # but expose the missing OCR dependency so the
                                # job cannot claim that the page was read.
                                stripped = text.strip()
                                result["pages"].append({
                                    "page_number": page_num + 1,
                                    "text": stripped,
                                    "text_length": len(stripped),
                                    "method": ("text_layer_fallback" if stripped
                                               else "ocr_required_engine_unavailable"),
                                    "ocr_status": "ocr_required_engine_unavailable",
                                    "error": (None if stripped else
                                              "OCR is required but no OCR engine is available")
                                })
                            else:
                                # Page needs OCR - ensure we still track the page number
                                try:
                                    mat = fitz.Matrix(2.0, 2.0)  # 2x resolution
                                    pix = page.get_pixmap(matrix=mat, alpha=False)
                                    img_bytes = pix.tobytes("png")
                                    
                                    page_data_list.append((
                                        page_num,
                                        img_bytes,
                                        tesseract_lang,
                                        tesseract_config,
                                        True,  # needs_ocr
                                        ocr_languages,  # languages for per-page detection
                                        text,  # native text layer, kept as fallback
                                        # PDF-02: the reader instance is shared
                                        # (one per repository) and these tuples are
                                        # consumed on executor threads, so neither
                                        # ``self`` nor a thread-local can carry the
                                        # file identity here. Pass it explicitly.
                                        str(filepath)
                                    ))
                                except Exception as page_error:
                                    # If we can't convert page to image, store empty page
                                    logger.warning(f"Failed to convert page {page_num + 1} to image: {page_error}")
                                    result["pages"].append({
                                        "page_number": page_num + 1,
                                        "text": "",
                                        "text_length": 0,
                                        "method": "conversion_failed",
                                        "error": str(page_error)
                                    })
                        except Exception as page_error:
                            # If we can't process a page, store empty page to preserve order
                            logger.warning(f"Failed to process page {page_num + 1}: {page_error}")
                            result["pages"].append({
                                "page_number": page_num + 1,
                                "text": "",
                                "text_length": 0,
                                "method": "processing_failed",
                                "error": str(page_error)
                            })
                finally:
                    # Always close the document to prevent resource leaks
                    doc.close()
                
                # ===== PARALLEL OCR PROCESSING =====
                result["ocr_used"] = bool(page_data_list)
                if page_data_list:
                    logger.info(f"Processing {len(page_data_list)} pages with OCR...")
                    
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = {
                            executor.submit(self.process_page_optimized, page_data): page_data[0]
                            for page_data in page_data_list
                        }
                        
                        completed_count = 0
                        for future in as_completed(futures):
                            try:
                                # Yield periodically during OCR processing
                                completed_count += 1
                                self.yield_periodically(completed_count, interval=3, aggressive=True)
                                
                                page_result = future.result()
                                result["pages"].append(page_result)
                            except Exception as ocr_error:
                                # If OCR fails for a page, we should still have the page number
                                # from the page_data, but we need to extract it
                                logger.warning(f"OCR processing failed for a page: {ocr_error}")
                                # The page_result should still have page_number from process_page_optimized
                                # If not, we can't recover it here, so we'll skip it
                                # The page will be missing, but that's better than crashing
            
            # Sort pages by page number
            result["pages"].sort(key=lambda x: x["page_number"])
            
            # Calculate statistics
            result["total_characters"] = sum(p.get("text_length", 0) for p in result["pages"])
            result["avg_chars_per_page"] = (
                result["total_characters"] / len(result["pages"]) 
                if result["pages"] else 0
            )
            
            # Success rate
            successful_pages = sum(1 for p in result["pages"] if p.get("text_length", 0) > 0)
            result["success_rate"] = f"{successful_pages / len(result['pages']) * 100:.1f}%" if result["pages"] else "0%"
            
            # Method breakdown
            methods = {}
            for page in result["pages"]:
                method = page.get("method", "unknown")
                methods[method] = methods.get(method, 0) + 1
            result["methods_used"] = methods
            
            # A missing OCR backend must remain visible to the job runner even
            # when a PDF also contains a native text layer.
            unavailable_pages = [
                p for p in result["pages"]
                if p.get("method") == "ocr_required_engine_unavailable"
            ]
            if unavailable_pages:
                result["extraction_failed"] = True
                result["retryable"] = True
                result["error"] = "OCR required but unavailable for one or more pages"
                result["ocr_status"] = "ocr_required_engine_unavailable"

            # Processing time
            result["processing_time"] = time.time() - start_time
            
            logger.info(f"PDF processed in {result['processing_time']:.2f}s: {filepath.name}")
            logger.info(f"  Methods: {methods}")
            logger.info(f"  Success rate: {result['success_rate']}")
            
            return result
        
        except Exception as e:
            return {
                "error": str(e),
                "filepath": str(filepath),
                "error_type": type(e).__name__,
                "processing_time": time.time() - start_time,
                "pages": [],
                "total_characters": 0
            }
    
    def batch_process_pdfs(self, pdf_paths, max_workers=4):
        """
        Process multiple PDFs in parallel
        
        Args:
            pdf_paths: List of PDF file paths
            max_workers: Number of parallel PDF processors
        
        Returns:
            List of results
        """
        results = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Create file_info for each PDF
            file_infos = [{"path": path} for path in pdf_paths]
            
            futures = {
                executor.submit(self.read_file, file_info): file_info
                for file_info in file_infos
            }
            
            for future in as_completed(futures):
                file_info = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info(f"Completed: {file_info['path']}")
                except Exception as e:
                    logger.error(f"Failed: {file_info['path']} - {e}")
                    results.append({
                        "error": str(e),
                        "filepath": file_info['path']
                    })
        
        return results

