"""Small helpers shared by every module: time, JSON files, text."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import unicodedata
from pathlib import Path

ID_RE = re.compile(r'\d{8}-\d{6}-[0-9a-f]{6}')


class Refuse(Exception):
    """A command refuses: the message goes to stderr and the exit code is 1. Nothing was changed. `stdout` sends the
    message to stdout instead: a state the caller reads (a busy resource), listed in `cli.stdout_refusals`."""

    def __init__(self, message='', stdout=False):
        super().__init__(message)
        self.stdout = stdout


# ---------------------------------------------------------------- time

def now() -> dt.datetime:
    """Local time without microseconds. Tests replace `ai_signalbox.util.clock` to move it."""
    return clock()


def _clock() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


clock = _clock


def now_iso() -> str:
    return now().isoformat()


def stamp(when: dt.datetime | None = None) -> str:
    """Event timestamp: local time with its offset, milliseconds."""
    return (when or dt.datetime.now().astimezone()).isoformat(timespec='milliseconds')


def new_id() -> str:
    """`YYYYMMDD-HHMMSS-xxxxxx`: sortable by time, unique enough across processes."""
    return f'{dt.datetime.now():%Y%m%d-%H%M%S}-{os.urandom(3).hex()}'


def hhmm(iso) -> str:
    try:
        return dt.datetime.fromisoformat(iso).strftime('%H:%M')
    except (TypeError, ValueError):
        return '?'


def aware(iso):
    try:
        return dt.datetime.fromisoformat(str(iso).replace('Z', '+00:00')).astimezone()
    except (AttributeError, ValueError, TypeError):
        return None


def local_hhmm(iso) -> str:
    t = aware(iso)
    return t.strftime('%H:%M') if t else '?'


def when_day(iso) -> str:
    t = aware(iso or '')
    if not t:
        return ''
    return 'today' if t.date() == dt.date.today() else t.strftime('%Y-%m-%d')


def age(iso) -> str:
    t = aware(iso or '')
    if not t:
        return '?'
    s = max(0, (dt.datetime.now().astimezone() - t).total_seconds())
    if s < 3600:
        return f'{int(s // 60)} min'
    if s < 86400:
        return f'{int(s // 3600)} h'
    d, h = divmod(int(s // 3600), 24)
    return f'{d} d {h} h' if h else f'{d} d'


# ---------------------------------------------------------------- files

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def write_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data), encoding='utf-8')


def write_atomic(path, data) -> None:
    """Write to a temp file and rename over the target: a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f'.{path.name}.{os.getpid()}.tmp'
    tmp.write_text(dumps(data), encoding='utf-8')
    os.replace(tmp, path)


def touch(path, text: str = '') -> None:
    """Rewrite a file in place (a rename would make a file watcher lose it). Never raises."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(f'{now().isoformat()} {text}\n')
    except OSError:
        pass


# ---------------------------------------------------------------- text

def first_line(text, width: int = 160) -> str:
    """The first non-empty line that is not a code fence, cut at `width`."""
    line = next((l.strip() for l in (text or '').splitlines()
                 if l.strip() and not re.fullmatch(r'`{3,}\w*', l.strip())), '')
    return line if len(line) <= width else line[:width] + '...'


def strip_accents(text) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', text or '') if unicodedata.category(c) != 'Mn').lower()


def one_line(text) -> str:
    return ' '.join((text or '').split())


def clean_text(text) -> str:
    """CRLF to LF, stripped: a file written on Windows gives the same text."""
    return (text or '').replace('\r\n', '\n').replace('\r', '\n').strip()


def title_and_body(text, title, what: str, cap: int):
    """Title and body of a request: without `title`, the first line is the title."""
    body = (text or '').strip()
    if not title:
        first, _, rest = body.partition('\n')
        title, body = first.lstrip('#').strip(), rest.strip()
    title = ' '.join((title or '').split())
    if not title:
        raise Refuse(f'{what} needs a one-line title: --title "..." or the first line of the text')
    if len(title) > 160:
        raise Refuse(f'title with {len(title)} characters: one line of up to 160')
    if len(body) > cap:
        raise Refuse(f'text with {len(body)} characters (cap {cap}): put the long part in a file and send the summary '
                     f'with its path')
    return title, body
