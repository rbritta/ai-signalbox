"""The notes of one release: its section of CHANGELOG.md (Keep a Changelog).

    python scripts/release_notes.py v1.2.3 > notes.md

release.yml runs it on a `v*` tag. It exits 1 with the reason when the tag does not name the version in
pyproject.toml, when `ai_signalbox.__version__` says something else, or when CHANGELOG.md has no section (or an empty
one) for that version.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def changelog_section(changelog: str, version: str) -> str | None:
    """The body of `## [version]`, trimmed; None when there is none or it is empty."""
    lines = changelog.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    head = re.compile(rf'^## \[{re.escape(version)}\](?:\s|$)')
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith('## ') or re.match(r'^\[[^\]]+\]:\s', lines[i])), len(lines))
    return '\n'.join(lines[start + 1:end]).strip() or None


def release_notes(tag: str, changelog: str, version: str) -> str:
    """The notes for `tag` (`v1.2.3`), which must name `version`. Raises ValueError with the reason."""
    named = re.sub(r'^refs/tags/', '', tag or '')
    named = named[1:] if named.startswith('v') else named
    if named != version:
        raise ValueError(f'the tag {tag or "(none)"} does not name the version in pyproject.toml, {version}')
    notes = changelog_section(changelog, version)
    if not notes:
        raise ValueError(f'CHANGELOG.md has no section, or an empty one, for {version}')
    return notes


def versions(root: Path = ROOT) -> tuple[str, str]:
    """(the version in pyproject.toml, `__version__` in the package)."""
    with open(root / 'pyproject.toml', 'rb') as fh:
        project = tomllib.load(fh)['project']['version']
    init = (root / 'src' / 'ai_signalbox' / '__init__.py').read_text(encoding='utf-8')
    m = re.search(r"^__version__ = ['\"]([^'\"]+)['\"]", init, re.M)
    return project, m.group(1) if m else ''


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        project, package = versions()
        if project != package:
            raise ValueError(f'pyproject.toml says {project} and ai_signalbox.__version__ says {package}')
        notes = release_notes(argv[0] if argv else '', (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'), project)
    except (OSError, ValueError, KeyError) as err:
        print(f'release-notes: {err}', file=sys.stderr)
        return 1
    sys.stdout.write(notes + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
