"""Trusted text worker. Input is bytes only; no user paths or credentials."""
from contextlib import closing
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEXT_LIMIT = 2000000


def pdf_pages(raw):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw), strict=False)
    if reader.is_encrypted: raise ValueError('encrypted_pdf')
    if len(reader.pages) > 200: raise ValueError('page_limit')
    pages = []
    total = 0
    for number, page in enumerate(reader.pages, 1):
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 12 * 1024 * 1024:
            raise ValueError('page_stream_limit')
        text = (page.extract_text() or '').replace('\x00', '')
        if len(text) > 120000: raise ValueError('page_text_limit')
        total += len(text)
        if total > TEXT_LIMIT: raise ValueError('text_limit')
        pages.append({'page': number, 'text': text, 'method': 'pdf_text'})
    missing = [page for page in pages if not page['text'].strip()]
    if not missing: return pages  # No renderer/model loaded for searchable PDFs.
    if len(missing) > 20: raise ValueError('ocr_page_limit')
    binary = shutil.which('tesseract')
    trained = ROOT / 'data' / 'ocr' / 'tessdata'
    if not binary or not all((trained / (lang + '.traineddata')).is_file() for lang in ('chi_sim', 'eng')):
        raise ValueError('ocr_unavailable')
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(raw) as pdf:
        with tempfile.TemporaryDirectory(prefix='ocr-page-') as directory:
            image_path = Path(directory) / 'page.png'
            output_path = Path(directory) / 'text'
            for target in missing:
                with closing(pdf[target['page'] - 1]) as page:
                    width, height = page.get_size()
                    if not all(math.isfinite(v) and 0 < v <= 20000 for v in (width, height)):
                        raise ValueError('page_stream_limit')
                    scale = min(150 / 72, math.sqrt(8000000 / (width * height)))
                    with closing(page.render(scale=scale)) as bitmap:
                        image = bitmap.to_pil()
                        try: image.save(image_path)
                        finally: image.close()
                try:
                    result = subprocess.run([binary, str(image_path), str(output_path), '--tessdata-dir',
                                             str(trained), '-l', 'chi_sim+eng', '--oem', '1', '--psm', '3'],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, timeout=30, check=False)
                except subprocess.TimeoutExpired: raise ValueError('ocr_timeout')
                text_path = output_path.with_suffix('.txt')
                if result.returncode or not text_path.is_file(): raise ValueError('ocr_failed')
                if text_path.stat().st_size > 480000: raise ValueError('page_text_limit')
                text = text_path.read_text(encoding='utf-8').replace('\x00', '').strip()
                if len(text) > 120000: raise ValueError('page_text_limit')
                total += len(text)
                if total > TEXT_LIMIT: raise ValueError('text_limit')
                target.update(text=text, method='local_ocr')
                image_path.unlink(missing_ok=True)
                text_path.unlink(missing_ok=True)
    return pages


def office_pages(raw, suffix):
    from defusedxml.ElementTree import fromstring
    # Never extract ZIP entries to disk or execute macros / external relationships.
    try: archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile: raise ValueError('invalid_office')
    with archive:
        entries = archive.infolist()
        if len(entries) > 5000 or sum(item.file_size for item in entries) > 64 * 1024 * 1024:
            raise ValueError('office_limit')
        if len({item.filename for item in entries}) != len(entries): raise ValueError('invalid_office')
        if any(item.flag_bits & 1 or item.file_size > 16 * 1024 * 1024 for item in entries): raise ValueError('office_limit')
        names = set(archive.namelist())
        def xml(name):
            if name not in names: raise ValueError('invalid_office')
            return fromstring(archive.read(name))
        def tag(node): return node.tag.rsplit('}', 1)[-1]
        def paragraphs(root):
            return '\n'.join(''.join(node.text or '' for node in paragraph.iter() if tag(node) == 't')
                             for paragraph in root.iter() if tag(paragraph) == 'p')
        def ordered_parts(manifest, relationships, item_tag, expected):
            links = {item.get('Id'): item.get('Target') for item in xml(relationships)
                     if item.get('TargetMode') != 'External'}
            parts = []
            base = 'xl' if suffix == '.xlsx' else 'ppt'
            import posixpath
            for item in xml(manifest).iter():
                if tag(item) != item_tag: continue
                identifier = item.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                target = links.get(identifier)
                if not target: raise ValueError('invalid_office')
                path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else base + '/' + target)
                if not re.fullmatch(expected, path) or path not in names: raise ValueError('invalid_office')
                parts.append((path, item.get('name', '')))
            if not parts or len(parts) > 200: raise ValueError('office_limit')
            return parts
        pages = []
        if suffix == '.docx':
            pages.append({'page': None, 'text': paragraphs(xml('word/document.xml')), 'method': 'office_text'})
        elif suffix == '.pptx':
            files = ordered_parts('ppt/presentation.xml', 'ppt/_rels/presentation.xml.rels',
                                  'sldId', r'ppt/slides/slide\d+\.xml')
            for number, (name, _) in enumerate(files, 1):
                text = paragraphs(xml(name))
                pages.append({'page': number, 'text': f'[幻灯片 {number}]\n' + text if text.strip() else '', 'method': 'office_text'})
        else:
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            shared = []
            if 'xl/sharedStrings.xml' in names:
                shared = [''.join(n.text or '' for n in item.iter() if tag(n) == 't')
                          for item in xml('xl/sharedStrings.xml')]
            files = ordered_parts('xl/workbook.xml', 'xl/_rels/workbook.xml.rels',
                                  'sheet', r'xl/worksheets/sheet\d+\.xml')
            for number, (name, title) in enumerate(files, 1):
                lines = []
                for row in xml(name).findall('.//s:row', ns):
                    cells = []
                    for cell in row.findall('s:c', ns):
                        kind = cell.get('t')
                        value = cell.find('s:v', ns)
                        text = (value.text or '') if value is not None else ''
                        if kind == 's':
                            if not text.isdigit() or int(text) >= len(shared): raise ValueError('invalid_office')
                            text = shared[int(text)]
                        elif kind == 'inlineStr': text = ''.join(n.text or '' for n in cell.iter() if tag(n) == 't')
                        # Formulas are never evaluated; only saved cached values are used.
                        if text: cells.append(f"{cell.get('r', '?')}: {text}")
                    if cells: lines.append(' | '.join(cells))
                pages.append({'page': number, 'text': f'[工作表 {number}：{title}]\n' + '\n'.join(lines) if lines else '', 'method': 'office_text'})
        return pages


def main():
    if os.name!='nt':
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    raw = sys.stdin.buffer.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024: raise ValueError('file_limit')
    suffix = sys.argv[1] if len(sys.argv) > 1 else '.pdf'
    pages = pdf_pages(raw) if suffix == '.pdf' else office_pages(raw, suffix)
    if sum(len(page['text']) for page in pages) > TEXT_LIMIT: raise ValueError('text_limit')
    print(json.dumps({'pages': pages}, ensure_ascii=False))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        codes = {'file_limit', 'encrypted_pdf', 'page_limit', 'page_stream_limit', 'page_text_limit', 'text_limit',
                 'ocr_unavailable', 'ocr_page_limit', 'ocr_timeout', 'ocr_failed', 'office_limit', 'invalid_office'}
        code = str(error) if isinstance(error, ValueError) and str(error) in codes else ('invalid_pdf' if len(sys.argv) < 2 or sys.argv[1] == '.pdf' else 'invalid_office')
        if isinstance(error, ImportError): code = 'parser_unavailable'
        print(json.dumps({'error': code})); sys.exit(1)
