"""明確建立 ZIP，再驗證最終附加元件；不將私人網頁資料封裝進去。"""
from pathlib import Path
import ast
import hashlib
import zipfile

root = Path(__file__).resolve().parent
source = root / 'addon'
files = sorted(path for path in source.rglob('*') if path.is_file() and '__pycache__' not in path.parts)
for path in files:
    if path.suffix == '.py':
        ast.parse(path.read_text(encoding='utf-8'), feature_version=(3, 13))
output = root / 'dist'
output.mkdir(exist_ok=True)
temporary = output / 'tableReader-1.0.zip'
final = output / 'tableReader-1.0.nvda-addon'
with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, path.relative_to(source).as_posix())
temporary.replace(final)
assert final.read_bytes().startswith(b'PK\x03\x04')
with zipfile.ZipFile(final) as archive:
    assert archive.testzip() is None
    assert 'manifest.ini' in archive.namelist()
    assert 'globalPlugins/tableReader/__init__.py' in archive.namelist()
    assert 'version = 1.0' in archive.read('manifest.ini').decode('utf-8')
    assert set(archive.namelist()) == {path.relative_to(source).as_posix() for path in files}
    for path in files:
        assert archive.read(path.relative_to(source).as_posix()) == path.read_bytes()
digest = hashlib.sha256(final.read_bytes()).hexdigest()
(output / 'SHA256.txt').write_text(digest + '  ' + final.name + '\n', encoding='utf-8')
print('Verified ZIP signature, CRC, manifest, entry point, version, exact file set and source equality.')
print(final)
print(digest)
