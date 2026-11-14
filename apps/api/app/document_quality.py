"""Bounded local text heuristics; warnings are not OCR accuracy or semantic scores."""
import hashlib
import re


def assess(pages):
    texts = [str(page.get('text', '')) for page in pages]
    characters = sum(len(text) for text in texts)
    empty = sum(not text.strip() for text in texts)
    bad = sum(text.count('\ufffd') + text.count('\x00') for text in texts)
    normalized = [re.sub(r'\s+', '', text) for text in texts if text.strip()]
    fingerprints = [hashlib.sha256(text.encode()).hexdigest() for text in normalized]
    duplicates = len(fingerprints) - len(set(fingerprints))
    short_ocr = sum(page.get('method') == 'local_ocr' and len(str(page.get('text', '')).strip()) < 30 for page in pages)
    warnings = []
    if not characters or empty: warnings.append(f'{empty} 个文本单元无正文；请核对原文件。')
    if bad and bad / max(characters, 1) >= .01: warnings.append('替换字符较多，可能存在乱码或提取异常。')
    if short_ocr: warnings.append(f'{short_ocr} 页OCR文字过少，请对照原页检查；未再次运行OCR。')
    if duplicates: warnings.append(f'{duplicates} 个文本单元正文重复，可能是重复页或重复内容；未删除。')
    if 0 < characters < 40: warnings.append('正文较短，可能不足以支持当前需求；不是语义相关性评分。')
    return {'characters': characters, 'units': len(pages), 'empty_units': empty,
            'duplicate_units': duplicates, 'replacement_characters': bad, 'warnings': warnings,
            'notice': '仅本地文本启发式提示，不评判资料是否正确，也不代表OCR准确率。'}


def preview(conn, document):
    rows = conn.execute('SELECT content FROM chunks WHERE document_id=? ORDER BY ordinal LIMIT 200', (document['id'],)).fetchall()
    result = assess([{'text': row['content']} for row in rows])
    duplicates = conn.execute('SELECT id,filename FROM documents WHERE sha256=? AND id!=? LIMIT 20', (document['sha256'], document['id'])).fetchall()
    return {**result, 'document_sha256': document['sha256'], 'sampled_chunks': len(rows),
            'total_chunks': document['chunk_count'], 'sample_complete': document['chunk_count'] <= len(rows),
            'duplicate_documents': [{'id': row['id'], 'filename': row['filename']} for row in duplicates],
            'parse_warnings': document.get('warnings', []), 'provider_requests': 0}
