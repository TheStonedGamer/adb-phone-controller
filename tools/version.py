"""Read the application version without importing GUI/native dependencies."""
import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def app_version():
    tree = ast.parse((ROOT / 'src/adb_phone_controller/__init__.py').read_text())
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in statement.targets):
            version = ast.literal_eval(statement.value)
            if not re.fullmatch(r'\d+\.\d+\.\d+', version):
                raise ValueError('Use a numeric major.minor.patch version')
            return version
    raise ValueError('Missing application version')

if __name__ == '__main__':
    import os
    version = app_version()
    tag = os.environ.get('GITHUB_REF_NAME', '')
    if os.environ.get('GITHUB_REF_TYPE') == 'tag' and tag != f'v{version}':
        raise SystemExit(f'Tag {tag!r} must match source version v{version}')
    print(version)
