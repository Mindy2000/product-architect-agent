"""Hash built release assets without reading any user data."""
import hashlib,sys
from pathlib import Path
root=Path(sys.argv[1] if len(sys.argv)>1 else 'release-artifacts')
lines=[hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name for path in sorted(root.iterdir()) if path.is_file() and path.suffix in {'.zip','.dmg','.exe'}]
(root/'SHA256SUMS.txt').write_text('\n'.join(lines)+'\n')
