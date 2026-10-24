"""Validate and reproducibly package a clean Hermes directory plugin (stdlib only)."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    '__init__.py', 'plugin.yaml', 'config_schema.json', 'context.py', 'host.py',
    'ipc.py', 'life_security.py', 'native_agent.py', 'native_life.py', 'native_types.py', 'runtime.py', 'settings.py', 'shared_memory.py',
    'README.md', 'INSTALL_AGENT.md', 'LICENSE', 'NOTICE.md', 'CHANGELOG.md',
    'UPSTREAM.json', 'requirements.txt', 'requirements-dev.txt', 'pyproject.toml',
    '.gitignore', '.gitattributes',
}
TREES = {'core', 'assets', 'tests', 'docs', 'examples', 'scripts', '.github'}
EXCLUDED = {'__pycache__', '.pytest_cache', '.ruff_cache', '.git', '.venv', 'node_modules'}
PRIVATE_NAMES = {'credentials.json', 'settings.json', 'auth.json', 'memory.json',
                 'USER.md', 'MEMORY.md', 'SOUL.md', 'runtime.sock', 'runtime.lock'}


def source_files():
    files = []
    for path in sorted(ROOT.rglob('*')):
        rel = path.relative_to(ROOT)
        if rel.parts[0] not in ROOT_FILES | TREES or any(p in EXCLUDED for p in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError(f'Symlinks are not release sources: {rel}')
        if not path.is_file() or path.suffix in {'.pyc', '.pyo'}:
            continue
        if path.name in PRIVATE_NAMES or path.name.startswith('.env') or '.sqlite' in path.name:
            raise ValueError(f'Runtime/private file in source tree: {rel}')
        files.append(path)
    missing = [name for name in ROOT_FILES if not (ROOT/name).is_file()]
    if missing:
        raise ValueError(f'Missing required files: {missing}')
    return files


def validate(files):
    for path in files:
        if path.suffix == '.py':
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path.relative_to(ROOT)))
        elif path.suffix == '.json':
            json.loads(path.read_text(encoding='utf-8-sig'))
        elif path.suffix == '.toml':
            tomllib.loads(path.read_text(encoding='utf-8'))
    schema = json.loads((ROOT/'config_schema.json').read_text(encoding='utf-8-sig'))
    for name, value in schema.items():
        if (name.endswith('API_KEY') or name in {'SESSDATA', 'BILI_JCT', 'REFRESH_TOKEN', 'DEDE_USER_ID'}) and value.get('default'):
            raise ValueError(f'Credential default must be empty: {name}')
    upstream = json.loads((ROOT/'UPSTREAM.json').read_text())
    if not re.fullmatch('[a-f0-9]{40}', upstream['commit']):
        raise ValueError('Invalid upstream provenance commit')
    font = ROOT/'assets/fonts/NotoSansSC-VF.otf'
    if hashlib.sha256(font.read_bytes()).hexdigest() != 'd13ed01ec8aa45d6178999b648e96fb92150683e9f8e2a581f2acf208dcbe44b':
        raise ValueError('Bundled font differs from its provenance hash')
    if not (font.parent/'OFL.txt').exists():
        raise ValueError('Missing font license')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='validate without producing an archive')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    files = source_files()
    validate(files)
    match = re.search(r'^version:\s*([0-9.]+)\s*$', (ROOT/'plugin.yaml').read_text(), re.M)
    if not match:
        raise ValueError('Missing plugin version')
    version = match[1]
    if args.check:
        print(f'Validated {len(files)} source files (v{version}); no runtime files included')
        return
    output = args.output or ROOT/'dist'/f'hermes-bilibili-{version}.zip'
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'plugin': 'hermes-bilibili', 'version': version, 'sha256': {
        str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        entries = [(str(p.relative_to(ROOT)).replace('\\', '/'), p.read_bytes()) for p in files]
        entries.append(('SOURCE_MANIFEST.json', (json.dumps(manifest, indent=2, ensure_ascii=False)+'\n').encode()))
        for name, data in entries:
            info = zipfile.ZipInfo('hermes-bilibili/'+name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(f'{checksum}  {output.name}\n', encoding='ascii')
    print(f'{output}\nSHA256 {checksum}\n{len(files)} source files; manifest included')


if __name__ == '__main__':
    main()
