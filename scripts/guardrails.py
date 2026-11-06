"""Repository guardrails; no production operations or network calls."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
errors = []
required = ['README.md', 'Dockerfile', 'compose.yaml', '.env.example', 'docs/DEPLOYMENT.md', 'docs/TTS.md', 'tests/test_reader.py']
for file in required:
    if not (root / file).is_file():
        errors.append(f'Missing {file}')
static = root / 'app/static'
for path in static.glob('*'):
    content = path.read_text()
    if re.search(r'(?:src|href)=["\']https?://', content):
        errors.append(f'External frontend dependency: {path.name}')
    if 'innerHTML' in content:
        errors.append(f'Unsafe HTML rendering: {path.name}')
for folder in ('app', 'scripts', 'tests', '.github', 'deploy'):
    for path in (root / folder).rglob('*'):
        if not path.is_file() or '__pycache__' in str(path):
            continue
        content = path.read_text()
        if re.search(r'-----BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY-----', content):
            errors.append(f'Private key in {path}')
        if ('shell' + '=True') in content:
            errors.append(f'Shell execution in {path}')
workflow = (root / '.github/workflows/ci.yml').read_text()
if any(x in workflow for x in ('ssh ', 'scp ', 'docker push', 'pull_request_target')):
    errors.append('CI must not deploy or access production')
for value in ('30000', '20 * 1024 * 1024'):
    if value not in (root / 'app/main.py').read_text():
        errors.append(f'Upload/text limit missing: {value}')
if errors:
    raise SystemExit('\n'.join(errors))
print('Repository guardrails passed')
