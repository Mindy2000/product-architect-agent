"""Synthetic document parsing and local OCR: no external files or model APIs."""
import io,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app import document_runtime
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
from PIL import Image,ImageDraw,ImageFont


def zipped(items):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        for name,value in items.items():archive.writestr(name,value)
    return out.getvalue()


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        setting=patch.object(document_runtime,'DATA',Path(self.tmp.name));setting.start();self.addCleanup(setting.stop)
    def test_searchable_pdf_uses_text_path(self):
        writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content=DecodedStreamObject();content.set_data(b'BT /F1 24 Tf 50 700 Td (M19 Acceptance Document) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(content);out=io.BytesIO();writer.write(out)
        result=document_runtime.extract_document(out.getvalue(),'.pdf');self.assertEqual(result[0]['method'],'pdf_text');self.assertIn('Acceptance Document',result[0]['text'])
    def test_scanned_pdf_runs_free_local_ocr(self):
        if not document_runtime.capabilities()["enabled"]:self.skipTest("Optional Tesseract and pinned language data are not installed in this source checkout")
        image=Image.new('RGB',(1800,900),'white');draw=ImageDraw.Draw(image);draw.text((100,200),'ACCEPTANCE TASK STORAGE',font=ImageFont.load_default(size=80),fill='black')
        out=io.BytesIO();image.save(out,format='PDF',resolution=150);image.close()
        result=document_runtime.extract_document(out.getvalue(),'.pdf');self.assertEqual(result[0]['method'],'local_ocr');self.assertIn('ACCEPTANCE',result[0]['text'].upper())
    def test_encrypted_pdf_is_rejected(self):
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('synthetic-password');out=io.BytesIO();writer.write(out)
        with self.assertRaises(document_runtime.DocumentRuntimeError):document_runtime.extract_document(out.getvalue(),'.pdf')
    def test_docx_chinese_text(self):
        raw=zipped({'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>合成产品需求文档</w:t></w:r></w:p></w:body></w:document>'})
        self.assertIn('合成产品需求',document_runtime.extract_document(raw,'.docx')[0]['text'])
    def test_pptx_order_and_text(self):
        raw=zipped({'ppt/presentation.xml':'<p:presentation xmlns:p="urn:p" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId r:id="slide1"/></p:sldIdLst></p:presentation>','ppt/_rels/presentation.xml.rels':'<Relationships><Relationship Id="slide1" Target="slides/slide1.xml"/></Relationships>','ppt/slides/slide1.xml':'<a:root xmlns:a="urn:a"><a:p><a:r><a:t>任务验收幻灯片</a:t></a:r></a:p></a:root>'})
        result=document_runtime.extract_document(raw,'.pptx');self.assertEqual(result[0]['page'],1);self.assertIn('任务验收幻灯片',result[0]['text'])
    def test_xlsx_only_reads_cached_text(self):
        raw=zipped({'xl/workbook.xml':'<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="验收表" r:id="sheet1"/></sheets></workbook>','xl/_rels/workbook.xml.rels':'<Relationships><Relationship Id="sheet1" Target="worksheets/sheet1.xml"/></Relationships>','xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>验收结果</t></is></c><c r="B1"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>'})
        result=document_runtime.extract_document(raw,'.xlsx');self.assertIn('验收结果',result[0]['text']);self.assertIn('B1: 2',result[0]['text'])
    def test_xml_entity_expansion_is_rejected(self):
        raw=zipped({'word/document.xml':'<!DOCTYPE x [<!ENTITY x "unsafe">]><document><p><t>&x;</t></p></document>'})
        with self.assertRaises(document_runtime.DocumentRuntimeError):document_runtime.extract_document(raw,'.docx')


class ImageNormalizationTests(unittest.TestCase):
    def test_png_is_resized_and_metadata_removed_without_vision_call(self):
        from app import vision,model
        with tempfile.TemporaryDirectory() as tmp,patch.object(vision,'DATA',Path(tmp)/'vision'),patch.object(model,'DATA_DIR',Path(tmp)),patch.object(model,'USAGE_DB',Path(tmp)/'usage.sqlite3'):
            from PIL.PngImagePlugin import PngInfo
            image=Image.new('RGBA',(1000,700),(255,0,0,200));metadata=PngInfo();metadata.add_text('fixture','synthetic-private-metadata');out=io.BytesIO();image.save(out,format='PNG',pnginfo=metadata);image.close()
            registered=vision.register(out.getvalue())
            with Image.open(vision.artifact_path(registered['artifact_id'])) as normalized:
                self.assertEqual(normalized.mode,'RGB');self.assertLessEqual(max(normalized.size),512);self.assertNotIn('fixture',normalized.info)
    def test_non_png_jpeg_is_rejected_without_provider_call(self):
        from app import vision
        image=Image.new('RGB',(10,10));out=io.BytesIO();image.save(out,format='GIF');image.close()
        with self.assertRaises(vision.VisionError):vision.register(out.getvalue())

if __name__=='__main__':unittest.main()
