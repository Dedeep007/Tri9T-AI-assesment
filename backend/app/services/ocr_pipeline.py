import os
import pdfplumber
import pytesseract
from PIL import Image
import io

class OCRPipeline:
    @staticmethod
    def extract_pdf_elements(pdf_path: str):
        """
        Extracts structural text elements from a PDF file.
        Attempts native text extraction first via pdfplumber.
        If a page has very little or no native text, falls back to OCR via pytesseract.
        
        Returns:
            List of dicts, each representing a text line:
            {
                "text": str,
                "font_name": str or None,
                "font_size": float or None,
                "is_bold": bool,
                "page_num": int,
                "y_pos": float
            }
        """
        elements = []
        
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                page_num = page_idx + 1
                text = page.extract_text() or ""
                
                # Fallback to OCR if page seems scanned (very low character count)
                if len(text.strip()) < 50:
                    print(f"Page {page_num} seems scanned. Falling back to OCR.")
                    page_elements = OCRPipeline._ocr_page(page, page_num)
                    elements.extend(page_elements)
                    continue
                
                # Native parsing: Group characters into lines and extract formatting
                page_elements = OCRPipeline._parse_native_page(page, page_num)
                elements.extend(page_elements)
                
        # Clean up running headers/footers
        cleaned_elements = OCRPipeline._strip_headers_footers(elements)
        return cleaned_elements

    @staticmethod
    def _parse_native_page(page, page_num):
        """
        Processes a native PDF page, grouping characters into lines and recovering font info.
        """
        chars = page.chars
        if not chars:
            return []
            
        # Group chars by horizontal lines (small top coordinate tolerance)
        lines_dict = {}
        for char in chars:
            text = char.get("text", "")
            if not text.strip() and text != " ":
                continue
                
            # Round top coordinate to group characters on the same visual line
            top = round(char.get("top", 0), 1)
            
            # Find close line if existing
            matched_top = None
            for existing_top in lines_dict.keys():
                if abs(existing_top - top) < 3.0:  # line height threshold
                    matched_top = existing_top
                    break
                    
            if matched_top is None:
                matched_top = top
                lines_dict[matched_top] = []
                
            lines_dict[matched_top].append(char)
            
        page_elements = []
        
        # Sort lines from top to bottom
        for top_pos in sorted(lines_dict.keys()):
            line_chars = lines_dict[top_pos]
            # Sort characters from left to right
            line_chars = sorted(line_chars, key=lambda c: c.get("x0", 0))
            
            # Reconstruct string
            text = "".join([c.get("text", "") for c in line_chars]).strip()
            if not text:
                continue
                
            # Compute dominant font attributes
            font_names = {}
            font_sizes = {}
            bold_count = 0
            
            for c in line_chars:
                f_name = c.get("fontname", "")
                f_size = c.get("size", 10.0)
                
                font_names[f_name] = font_names.get(f_name, 0) + 1
                font_sizes[f_size] = font_sizes.get(f_size, 0) + 1
                
                # Check for boldness indicators
                is_b = False
                if f_name:
                    f_name_lower = f_name.lower()
                    if "bold" in f_name_lower or "black" in f_name_lower or "heavy" in f_name_lower or "-bold" in f_name_lower:
                        is_b = True
                if is_b:
                    bold_count += 1
            
            dom_font = max(font_names, key=font_names.get) if font_names else None
            dom_size = max(font_sizes, key=font_sizes.get) if font_sizes else 10.0
            is_bold = (bold_count / len(line_chars)) > 0.5 if line_chars else False
            
            page_elements.append({
                "text": text,
                "font_name": dom_font,
                "font_size": dom_size,
                "is_bold": is_bold,
                "page_num": page_num,
                "y_pos": float(top_pos)
            })
            
        return page_elements

    @staticmethod
    def _ocr_page(page, page_num):
        """
        Uses Tesseract OCR on a page when native text is not available.
        """
        page_elements = []
        try:
            # Render page to image
            pil_image = page.to_image(resolution=150).original
            
            # Run pytesseract OCR with layout info (hOCR or TSV)
            # We use tesseract's TSV format to get line coordinates and confidence
            data = pytesseract.image_to_data(pil_image, output_type=pytesseract.Output.DICT)
            
            # Reconstruct lines from TSV words
            # Tesseract groups words into blocks, paragraphs, and lines
            n_boxes = len(data['level'])
            current_line_id = None
            line_words = []
            
            for i in range(n_boxes):
                # We care about word level elements (level 5)
                if data['level'][i] == 5:
                    word = data['text'][i].strip()
                    if not word:
                        continue
                        
                    line_id = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
                    if current_line_id != line_id:
                        if line_words:
                            text_line = " ".join([w['text'] for w in line_words]).strip()
                            avg_y = sum([w['top'] for w in line_words]) / len(line_words)
                            if text_line:
                                page_elements.append({
                                    "text": text_line,
                                    "font_name": "OCR_Default",
                                    "font_size": 10.0,
                                    "is_bold": False,
                                    "page_num": page_num,
                                    "y_pos": float(avg_y)
                                })
                        current_line_id = line_id
                        line_words = []
                        
                    line_words.append({
                        "text": word,
                        "top": data['top'][i],
                        "height": data['height'][i]
                    })
                    
            # Add last line
            if line_words:
                text_line = " ".join([w['text'] for w in line_words]).strip()
                avg_y = sum([w['top'] for w in line_words]) / len(line_words)
                if text_line:
                    page_elements.append({
                        "text": text_line,
                        "font_name": "OCR_Default",
                        "font_size": 10.0,
                        "is_bold": False,
                        "page_num": page_num,
                        "y_pos": float(avg_y)
                    })
                    
        except Exception as e:
            print(f"Error performing OCR on page {page_num}: {e}")
            
        return page_elements

    @staticmethod
    def _strip_headers_footers(elements):
        """
        Detects and strips running page headers and footers.
        Common headers/footers usually appear near the top/bottom limits of page bounding box,
        and often contain matching content (e.g. document title or page number patterns).
        """
        if not elements:
            return []
            
        # Group elements by text to find common recurring top/bottom labels
        top_candidates = {}
        bottom_candidates = {}
        
        # Identify page dimensions approximately:
        # Standard PDF page height is ~792 or ~842. y_pos in pdfplumber starts from top (0).
        # Top margin is typically < 60, bottom margin is typically > 720.
        for el in elements:
            text = el["text"].strip()
            y = el["y_pos"]
            
            if y < 60:
                top_candidates[text] = top_candidates.get(text, 0) + 1
            elif y > 720:
                bottom_candidates[text] = bottom_candidates.get(text, 0) + 1
                
        # We strip elements that occur on > 30% of pages and are at margins
        # Or that match simple patterns like "Page X" or "X"
        total_pages = max([el["page_num"] for el in elements]) if elements else 1
        threshold = max(2, total_pages * 0.3)
        
        headers_to_strip = {text for text, cnt in top_candidates.items() if cnt >= threshold}
        footers_to_strip = {text for text, cnt in bottom_candidates.items() if cnt >= threshold}
        
        cleaned = []
        for el in elements:
            text = el["text"].strip()
            y = el["y_pos"]
            
            # Check for header
            if y < 60 and text in headers_to_strip:
                continue
            # Check for footer
            if y > 720 and (text in footers_to_strip or text.isdigit() or text.lower().startswith("page")):
                continue
                
            cleaned.append(el)
            
        return cleaned
