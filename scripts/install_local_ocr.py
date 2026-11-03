"""Install only pinned, free English/Simplified Chinese Tesseract data."""
import hashlib
import os
import shutil
import urllib.request
from pathlib import Path

import truststore

REVISION = '87416418657359cb625c412a48b6e1d6d41c29bd'
FILES = {
    'chi_sim.traineddata': 'a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730',
    'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',
}
ROOT = Path(__file__).resolve().parents[1]


def main():
    if not shutil.which('tesseract'):
        raise SystemExit('请先安装免费Tesseract：brew install tesseract')
    truststore.inject_into_ssl()
    directory = ROOT / 'data' / 'ocr' / 'tessdata'
    if any(path.is_symlink() for path in (ROOT / 'data', ROOT / 'data' / 'ocr', directory)):
        raise SystemExit('OCR目录不能使用符号链接。')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, expected in FILES.items():
        target = directory / name
        if target.is_symlink(): raise SystemExit('OCR数据不能使用符号链接。')
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            print(name + ' 已安装，复用本地文件。'); continue
        url = f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{REVISION}/{name}'
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
            raise SystemExit('语言包校验失败，未安装。')
        temporary = target.with_suffix('.download')
        with temporary.open('xb') as stream: stream.write(raw)
        os.replace(temporary, target)
        print(name + ' 安装完成。')
    license_path = directory / 'LICENSE'
    if not license_path.exists():
        with urllib.request.urlopen(f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{REVISION}/LICENSE', timeout=30) as response:
            license_path.write_bytes(response.read(32768))


if __name__ == '__main__': main()
