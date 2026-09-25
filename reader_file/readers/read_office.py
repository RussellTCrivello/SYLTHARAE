"""
Office file reader - Extract content from office documents
Aligned with database design principles - all functions within class
Supports: Word, Excel, PowerPoint, OpenDocument formats
"""

import os
import tempfile
import logging
from typing import Dict, Any, Optional, Set

from core.file_utils import (
    OPENPYXL_PATH_SUFFIXES, load_spreadsheet_workbook,
)

from .base_reader import BaseReader



logger = logging.getLogger(__name__)

#: Upper bound on retained CSV data rows. Materialising every row of a
#: multi-gigabyte CSV as Python lists costs several times the file size and
#: turns ingestion into a memory-exhaustion failure; rows past this point are
#: counted and reported as truncated instead.
MAX_CSV_ROWS = 200_000


class OfficeFileReader(BaseReader):
    """
    Reader for office document files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def _embedded_extraction_dir(self, source_path):
        """The directory where this document's embedded objects are materialised.

        Computed once per source document and cached, because
        get_extraction_name_file allocates a NEW numbered folder whenever the
        name already exists - calling it per image would scatter one document's
        embedded objects across image-count directories.
        """
        cached = getattr(self, '_embedded_dir_cache', None)
        if cached and cached[0] == source_path:
            return cached[1]
        from core.path_utils import get_extraction_name_file

        extract_dir = get_extraction_name_file(source_path, '')
        os.makedirs(extract_dir, exist_ok=True)
        self._embedded_dir_cache = (source_path, extract_dir)
        return extract_dir

    def _materialise_embedded_image(self, image_bytes, image_name, source_path,
                                    result=None):
        """Write one embedded image to disk so it can become a child object.

        Returns the written path.

        EMBED-02: each embedded image used to be written to a
        NamedTemporaryFile and unlinked straight after OCR, so the bytes never
        survived long enough to be an object in their own right. Their text was
        folded into the parent document and the image itself was discarded - no
        row, no hash, no parent, no hierarchy. Writing into the same kind of
        directory that archives and email attachments use lets the router run
        the image through the ordinary child pipeline instead.

        The name goes through the same Windows-safe component normalisation
        used for archive members: package part names are attacker-influenced and
        must not become reserved device names or over-long components.
        """
        from core.archive_safety import windows_safe_component

        extract_dir = self._embedded_extraction_dir(source_path)
        safe_name = windows_safe_component(os.path.basename(image_name)) or 'embedded_image'
        destination = os.path.join(extract_dir, safe_name)
        with open(destination, 'wb') as handle:
            handle.write(image_bytes)
        # Stamp the result so the router knows this document has materialised
        # children to recurse into. Callers that build their result elsewhere
        # (extract_images_from_pptx returns a bare list) pass None and stamp it
        # themselves once the list is attached.
        if result is not None:
            result["extraction_path"] = extract_dir
        return destination

    def _image_reader(self):
        """A cached ImageFileReader used to OCR embedded images.

        Created once per reader rather than once per image: a document with
        fifty embedded pictures must not construct fifty readers.
        """
        cached = getattr(self, '_image_reader_cache', None)
        if cached is None:
            from .read_img_fast import ImageFileReader

            cached = ImageFileReader()
            self._image_reader_cache = cached
        return cached

    def _ocr_embedded_image(self, image_path):
        """Run the standard image reader over one embedded image.

        EMBED-01: this used to be a module-level import of
        read_image_file_fast at six call sites (DOCX, XLSX, PPTX, ODT, ODS,
        ODP). But read_image_file_fast is a METHOD on ImageFileReader, so the
        import raised ImportError every time. Each site was wrapped in
        except Exception with a logger.debug, so the failure was invisible and
        embedded image extraction silently produced nothing for every Office
        and OpenDocument format.
        """
        return self._image_reader().read_image_file_fast(image_path)

    def get_supported_extensions(self) -> Set[str]:
        """Every Office/OpenDocument format this reader processes.

        The list is deliberately complete rather than convenient: templates and
        macro-enabled variants are the formats most often *missed* by
        extension-driven routing, and a document that reaches no reader is a
        document whose evidence silently disappears. Each variant below is
        routed to a real parse path in :meth:`read_file` - see the dispatch
        table there - and the macro-enabled ones additionally have their VBA
        project extracted and hashed.
        """
        return {
            # Microsoft Word formats
            '.docx',   # Word 2007+
            '.doc',    # Word 97-2003
            '.docm',   # Word Macro-Enabled Document
            '.dotx',   # Word Template (OOXML)
            '.dotm',   # Word Macro-Enabled Template (OOXML)
            '.dot',    # Word 97-2003 Template
            # Microsoft Excel formats
            '.xlsx',   # Excel 2007+
            '.xls',    # Excel 97-2003
            '.xlsm',   # Excel Macro-Enabled Workbook
            '.xlsb',   # Excel Binary Workbook
            '.xltx',   # Excel Template (OOXML)
            '.xltm',   # Excel Macro-Enabled Template (OOXML)
            '.xlt',    # Excel 97-2003 Template
            # Microsoft PowerPoint formats
            '.pptx',   # PowerPoint 2007+
            '.pptm',   # PowerPoint Macro-Enabled Presentation
            '.ppt',    # PowerPoint 97-2003
            '.potx',   # PowerPoint Template (OOXML)
            '.potm',   # PowerPoint Macro-Enabled Template (OOXML)
            '.pot',    # PowerPoint 97-2003 Template
            '.ppsx',   # PowerPoint Slideshow (OOXML)
            '.ppsm',   # PowerPoint Macro-Enabled Slideshow (OOXML)
            '.pps',    # PowerPoint 97-2003 Slideshow
            # OpenDocument formats (documents, spreadsheets, presentations,
            # their templates and drawings - all ODF packages)
            '.odt',    # OpenDocument Text
            '.ott',    # OpenDocument Text Template
            '.ods',    # OpenDocument Spreadsheet
            '.ots',    # OpenDocument Spreadsheet Template
            '.odp',    # OpenDocument Presentation
            '.otp',    # OpenDocument Presentation Template
            '.odg',    # OpenDocument Drawing
            # Other formats
            '.csv',    # Comma-Separated Values
            '.tsv',    # Tab-Separated Values
            '.rtf',    # Rich Text Format
            '.ole',    # OLE compound file whose payload is an unparsable document
        }
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read office file and extract content with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with office document content or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        # DETECT-01: dispatch on the content-verified type, not the filename.
        ext = self.effective_extension(file_info)
        
        try:
            result = None
            # Microsoft Word formats. Templates and macro-enabled variants are
            # the same package structure with a different content type, so they
            # share the document parse path; the forensic pass below adds the
            # macro project and the variant details.
            if ext in ('.docx', '.docm', '.dotx', '.dotm'):
                result = self.read_docx_file(file_path)
            elif ext in ('.doc', '.dot'):
                result = self.read_doc_file(file_path)
            # Microsoft Excel formats
            elif ext in ('.xlsx', '.xlsm', '.xltx', '.xltm'):
                result = self.read_xlsx_file(file_path)
            elif ext in ('.xls', '.xlsb', '.xlt'):
                result = self.read_xls_file(file_path)
            # Microsoft PowerPoint formats
            elif ext in ('.pptx', '.pptm', '.potx', '.potm', '.ppsx', '.ppsm'):
                result = self.read_pptx_file(file_path)
            elif ext in ('.ppt', '.pot', '.pps'):
                result = self.read_ppt_file(file_path)
            # OpenDocument formats. Drawings use the presentation parser: both
            # are ODF packages whose pages are draw:page elements.
            elif ext in ('.odt', '.ott'):
                result = self.read_odt_file(file_path)
            elif ext in ('.ods', '.ots'):
                result = self.read_ods_file(file_path)
            elif ext in ('.odp', '.otp', '.odg'):
                result = self.read_odp_file(file_path)
            # Other formats
            elif ext in ('.csv', '.tsv'):
                delimiter = '\t' if ext == '.tsv' else ','
                result = self.read_csv_file(file_path, delimiter=delimiter)
            elif ext == '.rtf':
                result = self.read_rtf_file(file_path)
            elif ext == '.ole':
                # A compound file whose contents are not an Office document.
                # Reported with its OLE stream inventory so the artifact is
                # recorded as unsupported-but-inspected rather than failed.
                return self._read_unrecognised_ole(file_path)
            else:
                error_msg = f"Unsupported office file type: {ext or file_path}"
                return self.handle_read_error(ValueError(error_msg), file_path, "read_file")

            return self._attach_forensic_detail(result, file_path, ext)
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")



    def read_docx_file(self, filepath):
        """
        Independent DOCX reader function with image extraction and OCR.
        Preserves original document order by interleaving paragraphs, tables, and other elements.
        """
        try:
            from docx import Document
            from docx.oxml.ns import qn
        except ImportError:
            return {
                "error": "python-docx not installed. Install with: pip install python-docx",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            doc = Document(filepath)
            
            result = {
                "filepath": filepath,
                "elements": [],  # Ordered list of all document elements (paragraphs, tables, etc.)
                "paragraphs": [],  # Keep for backward compatibility
                "tables": [],  # Keep for backward compatibility
                "total_paragraphs": 0,
                "total_tables": 0,
                "extracted_images": []
            }
            
            # Extract elements in document order to preserve original layout
            # Iterate through document body elements in order
            paragraph_idx = 0
            table_idx = 0
            element_position = 0
            
            # Get paragraph and table objects with their document positions
            # We need to track which paragraphs and tables we've already processed
            processed_paragraphs = set()
            processed_tables = set()
            
            for element in doc.element.body:
                # Check if element is a paragraph
                if element.tag == qn('w:p'):
                    # Find corresponding paragraph object
                    para = None
                    for p in doc.paragraphs:
                        if p._element == element and id(p) not in processed_paragraphs:
                            para = p
                            processed_paragraphs.add(id(p))
                            break
                    
                    if para:
                        para_text = para.text.strip()
                        # Include all paragraphs (even empty ones) to preserve spacing/formatting
                        para_data = {
                            "type": "paragraph",
                            "position": element_position,
                            "text": para_text,
                            "style": para.style.name if para.style else "Normal",
                            "index": paragraph_idx
                        }
                        result["elements"].append(para_data)
                        
                        # Also add to paragraphs list for backward compatibility (only non-empty)
                        if para_text:
                            result["paragraphs"].append({
                                "text": para_text,
                                "style": para.style.name if para.style else "Normal"
                            })
                        
                        paragraph_idx += 1
                        element_position += 1
                
                # Check if element is a table
                elif element.tag == qn('w:tbl'):
                    # Find corresponding table object
                    table = None
                    for t in doc.tables:
                        if t._element == element and id(t) not in processed_tables:
                            table = t
                            processed_tables.add(id(t))
                            break
                    
                    if table:
                        table_data = {
                            "type": "table",
                            "position": element_position,
                            "table_number": table_idx + 1,
                            "rows": []
                        }
                        
                        # A merged cell is the SAME tc element at several
                        # grid positions: horizontally (one row) or vertically
                        # (one column, several rows). python-docx hands back
                        # that same text for every position the merge covers,
                        # so a merged heading was stored - and displayed - as
                        # many times as it had columns or rows. The text is
                        # kept once, at the first position it covers; the
                        # remaining positions stay empty, which keeps the grid
                        # the same shape and leaves the reading order alone.
                        emitted_cells = []
                        for row in table.rows:
                            row_cells = []
                            for cell in row.cells:
                                if any(cell._tc is seen for seen in emitted_cells):
                                    row_cells.append("")
                                    continue
                                emitted_cells.append(cell._tc)
                                row_cells.append(cell.text)
                            table_data["rows"].append(row_cells)
                        
                        result["elements"].append(table_data)
                        result["tables"].append({
                            "table_number": table_idx + 1,
                            "rows": table_data["rows"]
                        })
                        table_idx += 1
                        element_position += 1
            
            result["total_paragraphs"] = len(result["paragraphs"])
            result["total_tables"] = len(result["tables"])
            
            # Extract and OCR images from the document
            try:
                import zipfile
                
                # DOCX files are ZIP archives - extract images from word/media/ folder
                with zipfile.ZipFile(filepath, 'r') as docx_zip:
                    # List all files in the archive
                    file_list = docx_zip.namelist()
                    
                    # Find all image files in word/media/ directory
                    image_files = [f for f in file_list if f.startswith('word/media/') and 
                                 any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    # Extract and process each image
                    for image_file in image_files:
                        try:
                            image_bytes = docx_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from DOCX")
            
            except Exception as e:
                logger.debug(f"Image extraction from DOCX failed (non-critical): {e}")
                # Don't fail the whole document if image extraction fails
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    # ------------------------------------------------------------------
    # Forensic detail: everything the document libraries do not return
    # ------------------------------------------------------------------
    def _attach_forensic_detail(self, result, filepath, extension):
        """Add properties, revisions, annotations, links and macros to a result.

        The body readers return the visible document. This method adds the rest
        of the evidence - document and custom properties, comments, tracked
        changes, hidden content, fields, bookmarks, headers/footers, external
        targets, embedded objects, formulas, notes, the package inventory and
        the VBA project - under four additive keys:

        ``artifact_metadata``
            Core/application/custom document properties.
        ``forensic_features``
            Relationships, package inventory and the per-application feature
            block, plus ``extraction_errors`` naming anything that could not be
            read.
        ``macros``
            Every VBA project with its digests, module list and analysis.
        ``forensic_text``
            The extracted detail flattened into searchable text, so a term
            inside a tracked deletion, a comment or a macro is indexed by the
            same pipeline that indexes the body.

        Failure never removes evidence: if a sub-extraction raises, the error is
        recorded in ``extraction_errors`` and the body result is returned
        unchanged.
        """
        if not isinstance(result, dict):
            return result
        if result.get('error') and not result.get('filepath'):
            return result

        import zipfile

        errors = []
        is_zip_package = False
        try:
            is_zip_package = zipfile.is_zipfile(filepath)
        except Exception as exc:
            errors.append({'scope': 'package', 'error': str(exc)})

        # Composition of the artifact, always recorded (cheap, and it is what
        # makes "which parts existed" answerable later).
        try:
            from core.formats import identify

            detection = identify(filepath)
            result['format_identification'] = detection.as_dict()
        except Exception as exc:
            errors.append({'scope': 'format_identification', 'error': str(exc)})

        app = None
        if is_zip_package:
            app = {'.docx': 'word', '.docm': 'word', '.dotx': 'word', '.dotm': 'word',
                   '.doc': 'word', '.dot': 'word',
                   '.xlsx': 'excel', '.xlsm': 'excel', '.xltx': 'excel',
                   '.xltm': 'excel', '.xls': 'excel', '.xlsb': 'excel',
                   '.xlt': 'excel',
                   '.pptx': 'powerpoint', '.pptm': 'powerpoint', '.potx': 'powerpoint',
                   '.potm': 'powerpoint', '.ppsx': 'powerpoint', '.ppsm': 'powerpoint',
                   '.ppt': 'powerpoint', '.pot': 'powerpoint', '.pps': 'powerpoint',
                   '.odt': 'odf', '.ott': 'odf', '.ods': 'odf', '.ots': 'odf',
                   '.odp': 'odf', '.otp': 'odf', '.odg': 'odf'}.get(extension)
            try:
                from core.forensics import extract_package_features, text_from_features

                features = extract_package_features(filepath, app=app)
                result['forensic_features'] = features
                result['artifact_metadata'] = features.get('properties', {})
                errors.extend(features.get('extraction_errors') or [])
                feature_text = text_from_features(features)
                if feature_text:
                    result['forensic_text'] = feature_text
            except Exception as exc:
                errors.append({'scope': 'package_features', 'error': str(exc)})

        # VBA: extracted for every Office container, macro-enabled or legacy.
        # ``macro_present`` is True even when decompression fails, so "no
        # macros" and "macros we could not read" cannot be confused.
        try:
            from core.forensics import extract_vba, macro_summary

            macros = extract_vba(filepath)
            if macros.get('macro_present') or macros.get('extraction_errors'):
                result['macros'] = {
                    **macro_summary(macros),
                    'modules': [
                        {'name': module.get('name'), 'stream': module.get('stream'),
                         'line_count': module.get('line_count'),
                         'md5': module.get('md5'), 'source': module.get('source')}
                        for project in macros.get('projects', [])
                        for module in (project.get('modules') or [])
                    ],
                }
                errors.extend(macros.get('extraction_errors') or [])
                source = str(macros.get('source_text') or '')
                if source:
                    result['macros']['source_text'] = source
                    existing = str(result.get('forensic_text') or '')
                    result['forensic_text'] = (existing + '\n\n' + source).strip()
        except Exception as exc:
            errors.append({'scope': 'macros', 'error': str(exc)})

        if errors:
            result.setdefault('extraction_errors', [])
            result['extraction_errors'] = list(result['extraction_errors']) + errors
        return result

    def _read_unrecognised_ole(self, filepath):
        """Record an OLE compound file that is not a recognised Office document.

        The streams are inventoried (names, sizes, digests of small streams) so
        the artifact is evidence-bearing even though no document parser claims
        it. Its outcome is ``unsupported`` with a reason, never a silent skip.
        """
        result = {
            'filepath': filepath,
            'ole_container': True,
            'streams': [],
            'extraction_errors': [],
        }
        try:
            import hashlib

            import olefile

            if not olefile.isOleFile(filepath):
                return {'error': 'Not an OLE compound file', 'filepath': filepath}
            with olefile.OleFileIO(filepath) as ole:
                for entry in ole.listdir():
                    name = '/'.join(entry)
                    try:
                        size = ole.get_size(entry)
                    except Exception:
                        size = None
                    record = {'name': name, 'size': size}
                    if size is not None and size <= 1024 * 1024:
                        try:
                            with ole.openstream(entry) as handle:
                                data = handle.read()
                            record['md5'] = hashlib.md5(data).hexdigest()
                            record['sha256'] = hashlib.sha256(data).hexdigest()
                        except Exception as exc:
                            record['error'] = str(exc)
                    result['streams'].append(record)
            result['stream_count'] = len(result['streams'])
        except Exception as exc:
            result['extraction_errors'].append({'scope': 'ole', 'error': str(exc)})
        return self._attach_forensic_detail(result, filepath, '.ole')

    def read_doc_file(self, filepath):
        """Independent DOC reader function - tries multiple methods."""
        if not os.path.exists(filepath):
            return {"error": "File not found", "filepath": filepath}
        
        # Try docx2txt
        try:
            import docx2txt
            # if logger:
            #     logger.debug("Attempting to read .doc with docx2txt", file_path=filepath)
            
            text = docx2txt.process(filepath)
            
            result = {
                "filepath": filepath,
                "text": text,
                "method": "docx2txt",
                "total_characters": len(text),
                "total_lines": len(text.splitlines())
            }
            
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
            if paragraphs:
                result["paragraphs"] = paragraphs
                result["total_paragraphs"] = len(paragraphs)
            
            return result
        except ImportError:
            logger.debug("docx2txt not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"docx2txt failed: {str(e)}", extra={"file_path": filepath})
        
        # Try antiword
        try:
            import subprocess
            import shutil
            
            antiword_path = shutil.which('antiword')
            if antiword_path:
                # if logger:
                #     logger.debug("Attempting to read .doc with antiword", file_path=filepath)
                
                result = subprocess.run(
                    [antiword_path, filepath],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0:
                    text = result.stdout
                    
                    doc_result = {
                        "filepath": filepath,
                        "text": text,
                        "method": "antiword",
                        "total_characters": len(text),
                        "total_lines": len(text.splitlines())
                    }
                    
                    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                    if paragraphs:
                        doc_result["paragraphs"] = paragraphs
                        doc_result["total_paragraphs"] = len(paragraphs)
                    
                    return doc_result
        except Exception as e:
            logger.debug(f"antiword failed: {str(e)}", extra={"file_path": filepath})
        
        # Try LibreOffice
        try:
            import subprocess
            import shutil
            import tempfile
            
            libreoffice_cmds = ['libreoffice', 'soffice']
            libreoffice_path = None
            
            for cmd in libreoffice_cmds:
                path = shutil.which(cmd)
                if path:
                    libreoffice_path = path
                    break
            
            if libreoffice_path:
                # if logger:
                #     logger.debug("Attempting to read .doc with LibreOffice", file_path=filepath)
                
                with tempfile.TemporaryDirectory() as tmpdir:
                    try:
                        result = subprocess.run(
                            [
                                libreoffice_path,
                                '--headless',
                                '--convert-to', 'txt',
                                '--outdir', tmpdir,
                                filepath
                            ],
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                        
                        if result.returncode == 0:
                            base_name = os.path.splitext(os.path.basename(filepath))[0]
                            txt_file = os.path.join(tmpdir, f"{base_name}.txt")
                            
                            if os.path.exists(txt_file):
                                with open(txt_file, 'r', encoding='utf-8', errors='ignore') as f:
                                    text = f.read()
                                
                                doc_result = {
                                    "filepath": filepath,
                                    "text": text,
                                    "method": "libreoffice",
                                    "total_characters": len(text),
                                    "total_lines": len(text.splitlines())
                                }
                                
                                paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                                if paragraphs:
                                    doc_result["paragraphs"] = paragraphs
                                    doc_result["total_paragraphs"] = len(paragraphs)
                                
                                return doc_result
                    except Exception as e:
                        logger.debug(f"LibreOffice conversion failed: {str(e)}", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"LibreOffice check failed: {str(e)}", extra={"file_path": filepath})
        
        # Try olefile
        try:
            import olefile
            
            if not olefile.isOleFile(filepath):
                return {"error": "File is not a valid OLE2 format .doc file", "filepath": filepath}
            
            # if logger:
            #     logger.debug("Attempting to read .doc with olefile", file_path=filepath)
            
            ole = olefile.OleFileIO(filepath)
            
            if ole.exists('WordDocument'):
                stream = ole.openstream('WordDocument')
                data = stream.read()
                
                text_parts = []
                current_text = []
                for byte in data:
                    if 32 <= byte <= 126 or byte in [9, 10, 13]:
                        current_text.append(chr(byte))
                    else:
                        if len(current_text) > 3:
                            text_parts.append(''.join(current_text))
                        current_text = []
                
                if current_text and len(current_text) > 3:
                    text_parts.append(''.join(current_text))
                
                text = ' '.join(text_parts)
                
                ole.close()
                
                if text.strip():
                    doc_result = {
                        "filepath": filepath,
                        "text": text,
                        "method": "olefile",
                        "total_characters": len(text),
                        "total_lines": len(text.splitlines()),
                        "note": "Basic text extraction - formatting may be lost"
                    }
                    
                    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                    if paragraphs:
                        doc_result["paragraphs"] = paragraphs
                        doc_result["total_paragraphs"] = len(paragraphs)
                    
                    return doc_result
                else:
                    ole.close()
                    return {"error": "Could not extract text from .doc file", "filepath": filepath}
            else:
                ole.close()
                return {"error": "WordDocument stream not found in .doc file", "filepath": filepath}
                
        except ImportError:
            logger.debug("olefile not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"olefile extraction failed: {str(e)}", extra={"file_path": filepath})
        
        # All methods failed
        error_msg = (
            "Could not read .doc file. Please install one of the following:\n"
            "  - textract: pip install textract (requires antiword or LibreOffice)\n"
            "  - python-docx2txt: pip install docx2txt\n"
            "  - olefile: pip install olefile\n"
            "Or install external tools:\n"
            "  - antiword (command-line tool)\n"
            "  - LibreOffice (soffice command)"
        )
        
        # if logger:
        #     logger.warning("All .doc reading methods failed", file_path=filepath)
        
        return {
            "error": error_msg,
            "filepath": filepath
        }


    #: Suffixes openpyxl accepts when it is handed a *path*.  Anything else has
    def _load_workbook_by_content(self, filepath):
        """Open a spreadsheet by its bytes, not by its filename.

        openpyxl refuses a *path* whose extension is not one of its supported
        formats before it looks at the content:

            openpyxl does not support .docx file format, please check you can
            open it with Excel first. Supported formats are:
            .xlsx,.xlsm,.xltx,.xltm

        That is the error a real corpus produced for an attachment named
        ``attachment_00520.docx`` whose bytes are an xlsx workbook - the router
        had already resolved the content-verified type to ``.xlsx`` and sent it
        here, and the reader then failed the file because of its *name*,
        storing it with no text at all.

        The extension check is skipped for file-like objects, so a stream is
        passed whenever the declared name is not a supported spreadsheet
        suffix. Content wins over the name, which is the whole point of
        content-based routing; files whose names are already correct keep the
        exact code path they had before.
        """
        suffix = os.path.splitext(filepath)[1].lower()
        if suffix not in OPENPYXL_PATH_SUFFIXES:
            logger.info(
                "Opening %s as a spreadsheet by content (declared suffix %r is "
                "not a supported openpyxl path suffix)",
                os.path.basename(filepath), suffix or "(none)",
            )
        return load_spreadsheet_workbook(filepath, data_only=True)

    def read_xlsx_file(self, filepath):
        """Independent XLSX reader function with image extraction and OCR."""
        try:
            import openpyxl
        except ImportError:
            return {
                "error": "openpyxl not installed. Install with: pip install openpyxl",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            workbook = self._load_workbook_by_content(filepath)
            
            result = {
                "filepath": filepath,
                "sheet_names": workbook.sheetnames,
                "sheets": {},
                "total_sheets": len(workbook.sheetnames),
                "extracted_images": []
            }
            
            for sheet_idx, sheet_name in enumerate(workbook.sheetnames):
                # Yield periodically during sheet processing
                self.yield_periodically(sheet_idx, interval=3, aggressive=False)
                sheet = workbook[sheet_name]
                sheet_data = {
                    "name": sheet_name,
                    "max_row": sheet.max_row,
                    "max_column": sheet.max_column,
                    "data": []
                }
                
                for row_idx, row in enumerate(sheet.iter_rows(values_only=True)):
                    # Yield periodically during row processing
                    if row_idx > 0 and row_idx % 100 == 0:
                        self.yield_cpu_if_needed(aggressive=False)
                    sheet_data["data"].append(list(row))
                
                result["sheets"][sheet_name] = sheet_data
            
            # Extract and OCR images from the workbook
            try:
                import zipfile
                
                # XLSX files are ZIP archives - extract images from xl/media/ folder
                with zipfile.ZipFile(filepath, 'r') as xlsx_zip:
                    # List all files in the archive
                    file_list = xlsx_zip.namelist()
                    
                    # Find all image files in xl/media/ directory
                    image_files = [f for f in file_list if f.startswith('xl/media/') and 
                                any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    # Extract and process each image
                    for image_file in image_files:
                        try:
                            image_bytes = xlsx_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from XLSX")
            
            except Exception as e:
                logger.debug(f"Image extraction from XLSX failed (non-critical): {e}")
                # Don't fail the whole document if image extraction fails
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def read_xls_file(self, filepath):
        """Independent XLS reader function."""
        try:
            import xlrd
        except ImportError:
            return {
                "error": (
                    "xlrd not installed (or wrong version). "
                    "Install with: pip install 'xlrd==1.2.0'"
                ),
                "filepath": filepath
            }

        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}

            workbook = xlrd.open_workbook(filepath)

            result = {
                "filepath": filepath,
                "sheet_names": workbook.sheet_names(),
                "sheets": {},
                "total_sheets": len(workbook.sheet_names())
            }

            for sheet_name in workbook.sheet_names():
                sheet = workbook.sheet_by_name(sheet_name)
                sheet_data = {
                    "name": sheet_name,
                    "nrows": sheet.nrows,
                    "ncols": sheet.ncols,
                    "data": []
                }

                for row_idx in range(sheet.nrows):
                    row_values = sheet.row_values(row_idx)
                    sheet_data["data"].append(row_values)

                result["sheets"][sheet_name] = sheet_data

            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def read_csv_file(self, filepath, delimiter=',', encoding='utf-8'):
        """Independent CSV reader function.

        Produces the structured shape (headers / rows / row_count /
        column_count) that ``pipeline.storage_pipeline`` expects for tabular
        content.

        ROUTE-01: ``.csv`` now routes here instead of the plain-text reader,
        so a row cap is applied - a list of lists costs several times the
        file size in Python objects, and an unbounded read would turn a very
        large CSV into a memory-exhaustion failure. Rows beyond
        ``MAX_CSV_ROWS`` are counted but not retained, and the truncation is
        recorded in the result rather than hidden.
        """
        try:
            import csv

            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}

            result = {
                "filepath": filepath,
                "delimiter": delimiter,
                "headers": [],
                "rows": []
            }

            encodings = [encoding, 'utf-8', 'latin-1', 'cp1252']

            for enc in encodings:
                try:
                    rows_seen = 0
                    truncated = False
                    with open(filepath, 'r', encoding=enc, newline='') as file:
                        reader = csv.reader(file, delimiter=delimiter)

                        try:
                            result["headers"] = next(reader)
                        except StopIteration:
                            return {"error": "Empty file", "filepath": filepath}

                        for row in reader:
                            rows_seen += 1
                            if rows_seen <= MAX_CSV_ROWS:
                                result["rows"].append(row)
                            else:
                                truncated = True

                    result["encoding_used"] = enc
                    result["row_count"] = rows_seen
                    result["rows_stored"] = len(result["rows"])
                    result["truncated"] = truncated
                    if truncated:
                        result["truncation_note"] = (
                            f"Only the first {MAX_CSV_ROWS} data rows were "
                            f"retained; the file holds {rows_seen}."
                        )
                        logger.warning(
                            "CSV %s truncated to %d of %d rows",
                            filepath, MAX_CSV_ROWS, rows_seen,
                        )
                    break
                except UnicodeDecodeError:
                    if enc == encodings[-1]:
                        raise
                    continue

            result.setdefault("row_count", len(result["rows"]))
            result.setdefault("rows_stored", len(result["rows"]))
            result.setdefault("truncated", False)
            result["column_count"] = len(result["headers"])

            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def extract_images_from_pptx(self, filepath: str):
        """Extract all images from PPTX and perform OCR."""
        extracted_images = []
        
        try:
            import zipfile
            # PPTX files are ZIP archives
            with zipfile.ZipFile(filepath, 'r') as pptx_zip:
                file_list = pptx_zip.namelist()
                
                # Find all image files in ppt/media/
                image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp']
                image_files = [f for f in file_list if f.startswith('ppt/media/') and 
                            any(f.lower().endswith(ext) for ext in image_extensions)]
                
                logger.info(f"Found {len(image_files)} images in PPTX")
                
                # Process each image
                for image_file in image_files:
                    try:
                        image_bytes = pptx_zip.read(image_file)
                        image_name = os.path.basename(image_file)
                        
                        # Try to perform OCR if function is available
                        try:
                            
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            # This helper returns a bare list rather than a
                            # result dict, so nothing is stamped here; the caller
                            # records extraction_path once the list is attached.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    extracted_images.append(ocr_result)
                        except ImportError:
                            # OCR not available, just record image info
                            extracted_images.append({
                                "name": image_name,
                                "size_bytes": len(image_bytes),
                                "note": "OCR not available"
                            })
                    
                    except Exception as e:
                        logger.warning(f"Failed to process embedded image {image_file}: {e}")
                        continue
            
            if extracted_images:
                logger.info(f"Successfully extracted {len(extracted_images)} images from PPTX")
        
        except Exception as e:
            logger.debug(f"Image extraction from PPTX failed: {e}")
        
        return extracted_images


    def read_pptx_file(self, filepath):
        """
        Comprehensive PPTX reader with full content extraction.
        Extracts text, tables, images, charts, SmartArt, notes, and comments.
        Preserves spatial order of elements.
        """
        try:
            from pptx import Presentation
            from pptx.enum.shapes import MSO_SHAPE_TYPE
        except ImportError:
            return {
                "error": "python-pptx not installed. Install with: pip install python-pptx",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            prs = Presentation(filepath)
            
            total_slides = len(prs.slides)
            
            result = {
                "filepath": filepath,
                "slides": [],
                "total_slides": total_slides,
                "extracted_images": [],
                "presentation_notes": []
            }
            
            # Log warning for very large presentations
            if total_slides > 100:
                logger.warning(f"Large presentation detected: {total_slides} slides. Processing may take longer.")
            
            # Process slides with error handling for each slide
            slides_processed = 0
            slides_failed = 0
            
            for slide_idx, slide in enumerate(prs.slides):
                # Yield periodically to prevent CPU spikes during slide processing
                self.yield_periodically(slide_idx, interval=5, aggressive=True)
                try:
                    slide_data = {
                        "slide_number": slide_idx + 1,
                        "elements": [],
                        "texts": [],  # All text content (backward compatibility)
                        "notes": None,  # Speaker notes
                        "comments": []  # Slide comments
                    }
                    
                    # Extract speaker notes
                    if slide.has_notes_slide:
                        try:
                            notes_slide = slide.notes_slide
                            notes_text_frame = notes_slide.notes_text_frame
                            if notes_text_frame and notes_text_frame.text.strip():
                                slide_data["notes"] = notes_text_frame.text.strip()
                                result["presentation_notes"].append({
                                    "slide_number": slide_idx + 1,
                                    "notes": notes_text_frame.text.strip()
                                })
                        except Exception as e:
                            logger.debug(f"Failed to extract notes from slide {slide_idx + 1}: {e}")
                    
                    # Collect all shapes with their positions
                    shapes_with_positions = []
                    
                    for shape_idx, shape in enumerate(slide.shapes):
                        # Yield periodically during shape processing
                        if shape_idx > 0 and shape_idx % 10 == 0:
                            self.yield_cpu_if_needed(aggressive=True)
                        # Yield periodically during shape processing
                        if shape_idx > 0 and shape_idx % 10 == 0:
                            self.yield_cpu_if_needed(aggressive=True)
                        # Get spatial position
                        top = getattr(shape, 'top', 0)
                        left = getattr(shape, 'left', 0)
                        width = getattr(shape, 'width', 0)
                        height = getattr(shape, 'height', 0)
                        
                        element_data = {
                            "type": None,
                            "position": shape_idx,
                            "spatial_order": (top, left),
                            "top": top,
                            "left": left,
                            "width": width,
                            "height": height
                        }
                        
                        # Determine shape type and extract content
                        shape_type = getattr(shape, 'shape_type', None)
                        
                        # 0. SKIP SHAPES THAT DON'T HAVE TEXT CONTENT
                        # Connector shapes (lines, arrows) don't have text
                        if shape_type == MSO_SHAPE_TYPE.LINE or shape_type == MSO_SHAPE_TYPE.FREEFORM:
                            # Skip connector shapes - they're visual elements without text
                            continue
                        
                        # GraphicFrame objects (SmartArt, embedded graphics) - handle
                        # separately, EXCEPT tables: a table shape IS a GraphicFrame
                        # and must fall through to the structured table extraction
                        # below (previously tables were silently skipped here).
                        if (shape_type == MSO_SHAPE_TYPE.GROUP or
                                str(type(shape).__name__) == 'GraphicFrame') and \
                                not getattr(shape, 'has_table', False):
                            # Try to extract text from GraphicFrame if it has text_frame
                            try:
                                if hasattr(shape, 'has_text_frame') and shape.has_text_frame:
                                    text_frame = shape.text_frame
                                    text_content = []
                                    for paragraph in text_frame.paragraphs:
                                        para_text = paragraph.text.strip()
                                        if para_text:
                                            text_content.append(para_text)
                                    if text_content:
                                        element_data["type"] = "graphic_frame"
                                        element_data["text"] = "\n".join(text_content)
                                        slide_data["texts"].extend(text_content)
                                        shapes_with_positions.append(element_data)
                            except Exception as e:
                                logger.debug(f"GraphicFrame extraction failed for shape {shape_idx}: {e}")
                            # Continue to next shape regardless
                            continue
                        
                        # 1. TABLES - Check first as they're most structured
                        if shape.has_table:
                            try:
                                table = shape.table
                                element_data["type"] = "table"
                                element_data["table_number"] = len([e for e in shapes_with_positions if e.get("type") == "table"]) + 1
                                element_data["rows"] = []
                                element_data["row_count"] = len(table.rows)
                                element_data["col_count"] = len(table.columns) if table.rows else 0
                                
                                for row_idx, row in enumerate(table.rows):
                                    row_data = []
                                    for cell_idx, cell in enumerate(row.cells):
                                        cell_text = cell.text.strip() if cell.text else ""
                                        row_data.append(cell_text)
                                        if cell_text:
                                            slide_data["texts"].append(cell_text)
                                    element_data["rows"].append(row_data)
                                
                                shapes_with_positions.append(element_data)
                                continue
                            except (AttributeError, ValueError) as e:
                                logger.debug(f"Table extraction failed for shape {shape_idx}: {e}")
                        
                        # 2. CHARTS
                        if shape.has_chart:
                            try:
                                chart = shape.chart
                                element_data["type"] = "chart"
                                element_data["chart_type"] = str(chart.chart_type)
                                element_data["chart_title"] = chart.chart_title.text_frame.text if chart.has_title else None
                                
                                # Extract chart data
                                element_data["chart_data"] = {
                                    "categories": [],
                                    "series": []
                                }
                                
                                # Extract series data
                                for series in chart.series:
                                    series_data = {
                                        "name": series.name,
                                        "values": [v for v in series.values] if hasattr(series, 'values') else []
                                    }
                                    element_data["chart_data"]["series"].append(series_data)
                                
                                if element_data["chart_title"]:
                                    slide_data["texts"].append(element_data["chart_title"])
                                
                                shapes_with_positions.append(element_data)
                                continue
                            except Exception as e:
                                logger.debug(f"Chart extraction failed for shape {shape_idx}: {e}")
                            
                        # 3. IMAGES/PICTURES
                        if shape_type == MSO_SHAPE_TYPE.PICTURE or hasattr(shape, 'image'):
                            try:
                                element_data["type"] = "image"
                                element_data["image_name"] = f"slide_{slide_idx + 1}_shape_{shape_idx + 1}"
                                
                                # Try to get image file extension/format
                                if hasattr(shape, 'image'):
                                    image = shape.image
                                    element_data["image_format"] = image.content_type
                                    element_data["image_filename"] = getattr(image, 'filename', None)
                                
                                shapes_with_positions.append(element_data)
                                continue
                            except Exception as e:
                                logger.debug(f"Image extraction failed for shape {shape_idx}: {e}")
                        
                        # 4. GROUP SHAPES - Recursively extract content
                        if shape_type == MSO_SHAPE_TYPE.GROUP:
                            try:
                                element_data["type"] = "group"
                                element_data["group_elements"] = []
                                
                                for grouped_shape in shape.shapes:
                                    if hasattr(grouped_shape, 'text') and grouped_shape.text.strip():
                                        element_data["group_elements"].append({
                                            "type": "text",
                                            "text": grouped_shape.text.strip()
                                        })
                                        slide_data["texts"].append(grouped_shape.text.strip())
                                
                                if element_data["group_elements"]:
                                    shapes_with_positions.append(element_data)
                                continue
                            except Exception as e:
                                logger.debug(f"Group shape extraction failed for shape {shape_idx}: {e}")
                            
                        # 5. TEXT FRAMES - Most common text content
                        if shape.has_text_frame:
                            try:
                                text_frame = shape.text_frame
                                text_content = []
                                
                                for paragraph in text_frame.paragraphs:
                                    para_text = paragraph.text.strip()
                                    if para_text:
                                        text_content.append(para_text)
                                
                                if text_content:
                                    element_data["type"] = "text_frame"
                                    element_data["text"] = "\n".join(text_content)
                                    element_data["paragraph_count"] = len(text_content)
                                    slide_data["texts"].extend(text_content)
                                    shapes_with_positions.append(element_data)
                                continue
                            except Exception as e:
                                logger.debug(f"Text frame extraction failed for shape {shape_idx}: {e}")
                        
                        # 6. SIMPLE TEXT (fallback - check with safe attribute access)
                        try:
                            if hasattr(shape, "text"):
                                text_value = getattr(shape, "text", None)
                                if text_value and str(text_value).strip():
                                    element_data["type"] = "text"
                                    element_data["text"] = str(text_value).strip()
                                    slide_data["texts"].append(str(text_value).strip())
                                    shapes_with_positions.append(element_data)
                                    continue
                        except (AttributeError, TypeError) as e:
                            logger.debug(f"Text extraction failed for shape {shape_idx}: {e}")
                            # Continue to next check
                        
                        # 7. PLACEHOLDER SHAPES (titles, content placeholders)
                        if hasattr(shape, 'placeholder_format'):
                            try:
                                placeholder = shape.placeholder_format
                                element_data["type"] = "placeholder"
                                element_data["placeholder_type"] = str(placeholder.type)
                                
                                if shape.has_text_frame and shape.text.strip():
                                    element_data["text"] = shape.text.strip()
                                    slide_data["texts"].append(shape.text.strip())
                                    shapes_with_positions.append(element_data)
                            except Exception as e:
                                logger.debug(f"Placeholder extraction failed for shape {shape_idx}: {e}")
                        
                    # Sort elements by spatial position (top-to-bottom, left-to-right)
                    shapes_with_positions.sort(key=lambda x: (x["spatial_order"][0], x["spatial_order"][1]))
                    
                    # Update position indices after sorting
                    for idx, element in enumerate(shapes_with_positions):
                        element["position"] = idx
                    
                    slide_data["elements"] = shapes_with_positions
                    result["slides"].append(slide_data)
                    slides_processed += 1
                    
                    # Log progress for large presentations
                    if total_slides > 50 and (slide_idx + 1) % 10 == 0:
                        logger.info(f"Processing presentation: {slides_processed}/{total_slides} slides completed...")
                    
                except Exception as slide_error:
                    # Log error but continue processing other slides
                    slides_failed += 1
                    logger.warning(
                        f"Failed to process slide {slide_idx + 1}/{total_slides}: {slide_error}. "
                        f"Continuing with remaining slides..."
                    )
                    # Add error slide data to preserve slide count
                    result["slides"].append({
                        "slide_number": slide_idx + 1,
                        "elements": [],
                        "texts": [],
                        "notes": None,
                        "comments": [],
                        "error": str(slide_error)[:200]  # Truncate long error messages
                    })
                    continue
            
            # Log summary
            if slides_failed > 0:
                logger.warning(
                    f"Presentation processing complete: {slides_processed}/{total_slides} slides processed successfully, "
                    f"{slides_failed} slides failed"
                )
            elif total_slides > 50:
                logger.info(f"Presentation processing complete: {slides_processed}/{total_slides} slides processed successfully")
            
            # Extract and OCR images from the PPTX archive (with error handling)
            try:
                result["extracted_images"] = self.extract_images_from_pptx(filepath)
                if result["extracted_images"]:
                    # The images were materialised during extraction; publish the
                    # directory so the router processes them as child objects.
                    result["extraction_path"] = self._embedded_extraction_dir(filepath)
            except Exception as img_error:
                logger.warning(f"Failed to extract images from PPTX: {img_error}. Continuing without images.")
                result["extracted_images"] = []
            
            return result
        
        except Exception as e:
            logger.error(f"Failed to read PPTX file: {e}")
            return {"error": str(e), "filepath": filepath}


    def read_ppt_file(self, filepath):
        """
        Read legacy .ppt files by converting to .pptx first.
        Tries LibreOffice conversion, then falls back to python-pptx.
        """
        # NOTE: this function used to start with ``if logger is None: logger =
        # logging.getLogger(__name__)``. Assigning the name anywhere in a
        # function makes it local for the whole function, so that line turned
        # the ``if`` into an UnboundLocalError before any work happened: every
        # legacy .ppt/.pot/.pps file failed to read. The module logger already
        # exists (line 25) and is what the rest of the module uses.
        if not os.path.exists(filepath):
            return {"error": "File not found", "filepath": filepath}
        
        # Try LibreOffice conversion (best option)
        try:
            import subprocess
            import shutil
            
            libreoffice_cmds = ['libreoffice', 'soffice']
            libreoffice_path = None
            
            for cmd in libreoffice_cmds:
                path = shutil.which(cmd)
                if path:
                    libreoffice_path = path
                    break
            
            if libreoffice_path:
                with tempfile.TemporaryDirectory() as tmpdir:
                    try:
                        result = subprocess.run(
                            [
                                libreoffice_path,
                                '--headless',
                                '--convert-to', 'pptx',
                                '--outdir', tmpdir,
                                filepath
                            ],
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                        
                        if result.returncode == 0:
                            base_name = os.path.splitext(os.path.basename(filepath))[0]
                            pptx_file = os.path.join(tmpdir, f"{base_name}.pptx")
                            
                            if os.path.exists(pptx_file):
                                pptx_result = self.read_pptx_file(pptx_file)
                                if "error" not in pptx_result:
                                    pptx_result["filepath"] = filepath
                                    pptx_result["method"] = "libreoffice_conversion"
                                    return pptx_result
                    except Exception as e:
                        logger.debug(f"LibreOffice conversion failed: {e}")
        except Exception as e:
            logger.debug(f"LibreOffice not available: {e}")
        
        # Fallback: Try python-pptx directly
        try:
            from pptx import Presentation
            prs = Presentation(filepath)
            
            result = {
                "filepath": filepath,
                "slides": [],
                "total_slides": len(prs.slides),
                "method": "python-pptx_direct"
            }
            
            for slide_idx, slide in enumerate(prs.slides):
                slide_data = {
                    "slide_number": slide_idx + 1,
                    "texts": []
                }
                
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        slide_data["texts"].append(shape.text)
                
                result["slides"].append(slide_data)
            
            return result
        except Exception as e:
            logger.debug(f"python-pptx direct read failed: {e}")
        
        # All methods failed
        return {
            "error": "Could not read .ppt file. Please install LibreOffice or ensure python-pptx is installed.",
            "filepath": filepath
        }


    def read_odt_file(self, filepath):
        """Independent ODT (OpenDocument Text) reader function."""
        try:
            from odf.opendocument import load
            from odf.text import P
            from odf.table import Table, TableRow, TableCell
        except ImportError:
            return {
                "error": "odfpy not installed. Install with: pip install odfpy",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            doc = load(filepath)
            
            result = {
                "filepath": filepath,
                "paragraphs": [],
                "tables": [],
                "total_paragraphs": 0,
                "total_tables": 0,
                "extracted_images": []
            }
            
            # Extract paragraphs
            for paragraph in doc.getElementsByType(P):
                text = paragraph.childNodes[0].data if paragraph.childNodes else ""
                if text.strip():
                    result["paragraphs"].append({
                        "text": text,
                        "style": paragraph.getAttribute("stylename") or "Normal"
                    })
            
            result["total_paragraphs"] = len(result["paragraphs"])
            
            # Extract tables
            for table_idx, table in enumerate(doc.getElementsByType(Table)):
                table_data = {
                    "table_number": table_idx + 1,
                    "rows": []
                }
                
                for row in table.getElementsByType(TableRow):
                    row_data = []
                    for cell in row.getElementsByType(TableCell):
                        cell_text = ""
                        for para in cell.getElementsByType(P):
                            if para.childNodes:
                                cell_text += para.childNodes[0].data
                        row_data.append(cell_text)
                    table_data["rows"].append(row_data)
                
                result["tables"].append(table_data)
            
            result["total_tables"] = len(result["tables"])
            
            # Extract and OCR images from the document
            try:
                import zipfile
                
                # ODT files are ZIP archives - extract images from Pictures/ folder
                with zipfile.ZipFile(filepath, 'r') as odt_zip:
                    file_list = odt_zip.namelist()
                    image_files = [f for f in file_list if 'Pictures/' in f and 
                                any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    for image_file in image_files:
                        try:
                            image_bytes = odt_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from ODT")
            except Exception as e:
                logger.debug(f"Image extraction from ODT failed (non-critical): {e}")
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def read_ods_file(self, filepath):
        """Independent ODS (OpenDocument Spreadsheet) reader function."""
        try:
            from odf.opendocument import load
            from odf.table import Table, TableRow, TableCell
        except ImportError:
            return {
                "error": "odfpy not installed. Install with: pip install odfpy",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            doc = load(filepath)
            
            result = {
                "filepath": filepath,
                "sheet_names": [],
                "sheets": {},
                "total_sheets": 0,
                "extracted_images": []
            }
            
            # Extract tables (sheets)
            for table_idx, table in enumerate(doc.getElementsByType(Table)):
                sheet_name = table.getAttribute("name") or f"Sheet{table_idx + 1}"
                result["sheet_names"].append(sheet_name)
                
                sheet_data = {
                    "name": sheet_name,
                    "data": []
                }
                
                for row in table.getElementsByType(TableRow):
                    row_data = []
                    for cell in row.getElementsByType(TableCell):
                        cell_text = ""
                        # Get text from all paragraphs in cell
                        for para in cell.childNodes:
                            if hasattr(para, 'data'):
                                cell_text += para.data
                            elif hasattr(para, 'childNodes') and para.childNodes:
                                cell_text += para.childNodes[0].data if hasattr(para.childNodes[0], 'data') else ""
                        row_data.append(cell_text)
                    sheet_data["data"].append(row_data)
                
                result["sheets"][sheet_name] = sheet_data
            
            result["total_sheets"] = len(result["sheets"])
            
            # Extract and OCR images from the spreadsheet
            try:
                import zipfile
                
                # ODS files are ZIP archives - extract images from Pictures/ folder
                with zipfile.ZipFile(filepath, 'r') as ods_zip:
                    file_list = ods_zip.namelist()
                    image_files = [f for f in file_list if 'Pictures/' in f and 
                                any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    for image_file in image_files:
                        try:
                            image_bytes = ods_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from ODS")
            except Exception as e:
                logger.debug(f"Image extraction from ODS failed (non-critical): {e}")
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def read_odp_file(self, filepath):
        """
        Independent ODP (OpenDocument Presentation) reader function.
        Preserves original order of elements within slides.
        """
        try:
            from odf.opendocument import load
            from odf.draw import Page, TextBox
            from odf.text import P
            from odf.table import TableRow, TableCell
        except ImportError:
            return {
                "error": "odfpy not installed. Install with: pip install odfpy",
                "filepath": filepath
            }
        
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found", "filepath": filepath}
            
            doc = load(filepath)
            
            result = {
                "filepath": filepath,
                "slides": [],
                "total_slides": 0,
                "extracted_images": []
            }
            
            # Extract slides (pages) with elements in order
            pages = doc.getElementsByType(Page)
            for slide_idx, page in enumerate(pages):
                slide_data = {
                    "slide_number": slide_idx + 1,
                    "elements": [],  # Ordered list of all elements in the slide
                    "texts": []  # Keep for backward compatibility
                }
                
                element_position = 0
                
                # Extract elements in document order
                # Process all child elements of the page
                for child in page.childNodes:
                    if not hasattr(child, 'tagName'):
                        continue
                    
                    element_data = {
                        "type": None,
                        "position": element_position
                    }
                    
                    # Check for text boxes (frames with text)
                    if child.tagName == 'draw:frame' or (hasattr(child, 'tagName') and 'frame' in str(child.tagName)):
                        # Extract text from text boxes
                        text_boxes = child.getElementsByType(TextBox) if hasattr(child, 'getElementsByType') else []
                        text_content = []
                        
                        for text_box in text_boxes:
                            for para in text_box.getElementsByType(P) if hasattr(text_box, 'getElementsByType') else []:
                                if para.childNodes:
                                    para_text = para.childNodes[0].data if hasattr(para.childNodes[0], 'data') else ""
                                    if para_text.strip():
                                        text_content.append(para_text.strip())
                        
                        # Also check for direct paragraph children
                        for para in child.getElementsByType(P) if hasattr(child, 'getElementsByType') else []:
                            if para.childNodes:
                                para_text = para.childNodes[0].data if hasattr(para.childNodes[0], 'data') else ""
                                if para_text.strip():
                                    text_content.append(para_text.strip())
                        
                        if text_content:
                            element_data["type"] = "text"
                            element_data["text"] = "\n".join(text_content)
                            slide_data["texts"].extend(text_content)
                    
                    # Check for tables
                    elif child.tagName == 'table:table' or (hasattr(child, 'tagName') and 'table' in str(child.tagName)):
                        table = child if hasattr(child, 'getElementsByType') else None
                        if table:
                            element_data["type"] = "table"
                            element_data["table_number"] = len([e for e in slide_data["elements"] if e.get("type") == "table"]) + 1
                            element_data["rows"] = []
                            
                            for row in table.getElementsByType(TableRow) if hasattr(table, 'getElementsByType') else []:
                                row_data = []
                                for cell in row.getElementsByType(TableCell) if hasattr(row, 'getElementsByType') else []:
                                    cell_text = ""
                                    for para in cell.getElementsByType(P) if hasattr(cell, 'getElementsByType') else []:
                                        if para.childNodes:
                                            cell_text += para.childNodes[0].data if hasattr(para.childNodes[0], 'data') else ""
                                    row_data.append(cell_text.strip())
                                element_data["rows"].append(row_data)
                    
                    # Check for images
                    elif 'image' in str(child.tagName).lower():
                        element_data["type"] = "image"
                        element_data["image_name"] = f"slide_{slide_idx + 1}_element_{element_position + 1}"
                    
                    # Only add element if it has a recognized type
                    if element_data["type"]:
                        slide_data["elements"].append(element_data)
                        element_position += 1
                
                # Fallback: if no elements found, extract paragraphs directly
                if not slide_data["elements"]:
                    for para in page.getElementsByType(P):
                        if para.childNodes:
                            text = para.childNodes[0].data if hasattr(para.childNodes[0], 'data') else ""
                            if text.strip():
                                slide_data["texts"].append(text.strip())
                
                result["slides"].append(slide_data)
            
            result["total_slides"] = len(result["slides"])
            
            # Extract and OCR images from the presentation
            try:
                import zipfile
                
                # ODP files are ZIP archives - extract images from Pictures/ folder
                with zipfile.ZipFile(filepath, 'r') as odp_zip:
                    file_list = odp_zip.namelist()
                    image_files = [f for f in file_list if 'Pictures/' in f and 
                                any(f.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'])]
                    
                    for image_file in image_files:
                        try:
                            image_bytes = odp_zip.read(image_file)
                            image_name = os.path.basename(image_file)
                            
                            # Process image with OCR using existing function
                            # Materialise the embedded image into the document's
                            # extraction directory, then OCR it in place. Writing
                            # it out - rather than to a temp file that is unlinked
                            # immediately - is what lets the router treat the image
                            # as a child object with its own row, hash and lineage.
                            tmp_path = self._materialise_embedded_image(
                                image_bytes, image_name, filepath, result
                            )
                            ocr_result = self._ocr_embedded_image(tmp_path)
                            if ocr_result:
                                ocr_result["name"] = image_name
                                if ocr_result.get("text") or ocr_result.get("error"):
                                    result["extracted_images"].append(ocr_result)
                        except Exception as e:
                            logger.warning(f"Failed to process embedded image {image_file}: {e}")
                            continue
                
                if result["extracted_images"]:
                    logger.info(f"Extracted and processed {len(result['extracted_images'])} images from ODP")
            except Exception as e:
                logger.debug(f"Image extraction from ODP failed (non-critical): {e}")
            
            return result
        except Exception as e:
            return {"error": str(e), "filepath": filepath}


    def read_rtf_file(self, filepath):
        """Independent RTF (Rich Text Format) reader function."""
        if not os.path.exists(filepath):
            return {"error": "File not found", "filepath": filepath}
        
        # Try RTFDE (recommended)
        try:
            from RTFDE import deRTF
            with open(filepath, 'rb') as f:
                rtf_content = f.read()
            
            try:
                text = deRTF(rtf_content)
                
                result = {
                    "filepath": filepath,
                    "text": text,
                    "method": "RTFDE",
                    "total_characters": len(text),
                    "total_lines": len(text.splitlines())
                }
                
                paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                if paragraphs:
                    result["paragraphs"] = paragraphs
                    result["total_paragraphs"] = len(paragraphs)
                
                return result
            except Exception as e:
                logger.debug(f"RTFDE processing failed: {str(e)}", extra={"file_path": filepath})
        except ImportError:
            logger.debug("RTFDE not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"RTFDE import/execution failed: {str(e)}", extra={"file_path": filepath})
        
        # Try striprtf
        try:
            from striprtf.striprtf import rtf_to_text
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                rtf_content = f.read()
            
            text = rtf_to_text(rtf_content)
            
            result = {
                "filepath": filepath,
                "text": text,
                "method": "striprtf",
                "total_characters": len(text),
                "total_lines": len(text.splitlines())
            }
            
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
            if paragraphs:
                result["paragraphs"] = paragraphs
                result["total_paragraphs"] = len(paragraphs)
            
            return result
        except ImportError:
            logger.debug("striprtf not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"striprtf processing failed: {str(e)}", extra={"file_path": filepath})
        
        # Try compressed-rtf
        try:
            from compressed_rtf import compressed_rtf_to_text
            with open(filepath, 'rb') as f:
                rtf_content = f.read()
            
            text = compressed_rtf_to_text(rtf_content)
            
            result = {
                "filepath": filepath,
                "text": text,
                "method": "compressed-rtf",
                "total_characters": len(text),
                "total_lines": len(text.splitlines())
            }
            
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
            if paragraphs:
                result["paragraphs"] = paragraphs
                result["total_paragraphs"] = len(paragraphs)
            
            return result
        except ImportError:
            logger.debug("compressed-rtf not available, trying next method", extra={"file_path": filepath})
        except Exception as e:
            logger.debug(f"compressed-rtf processing failed: {str(e)}", extra={"file_path": filepath})
        
        # Try basic text extraction (fallback)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # Simple RTF text extraction (removes RTF control words)
            import re
            text = re.sub(r'\\[a-z]+\d*\s?', '', content)
            text = re.sub(r'\{[^}]*\}', '', text)
            text = text.replace('\\par', '\n').replace('\\line', '\n')
            text = ' '.join(text.split())
            
            if text.strip():
                result = {
                    "filepath": filepath,
                    "text": text,
                    "method": "basic_extraction",
                    "note": "Basic text extraction - formatting may be lost",
                    "total_characters": len(text),
                    "total_lines": len(text.splitlines())
                }
                
                paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                if paragraphs:
                    result["paragraphs"] = paragraphs
                    result["total_paragraphs"] = len(paragraphs)
                
                return result
        except Exception as e:
            logger.debug(f"Basic RTF extraction failed: {str(e)}", extra={"file_path": filepath})
        
        # All methods failed
        error_msg = (
            "Could not read .rtf file. Please install one of the following:\n"
            "  - RTFDE: pip install RTFDE (recommended)\n"
            "  - striprtf: pip install striprtf\n"
            "  - compressed-rtf: pip install compressed-rtf"
        )
        
        return {
            "error": error_msg,
            "filepath": filepath
        }

