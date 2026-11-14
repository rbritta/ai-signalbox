"""`signalbox serve`: the threads over HTTP, in chat-thread's REST store protocol, plus a stream of new turns.

Off by default; bound to 127.0.0.1 and guarded by a token (`Authorization: Bearer <token>` or `?token=`). A page then
needs only `createChat(el, { store: restStore(url) })` from chat-thread.

    GET  /api/chat                      {threads: [{thread, count, last, workspace, state, title}]}
    GET  /api/chat/<thread>?limit&before {messages}, oldest first
    POST /api/chat/<thread>   {text}    the person's turn in an open thread; the message, from 'me'
    POST /api/chat/<workspace> {text}   a new message to a workspace: opens a thread; the message, from 'me'
    GET  /api/events                    text/event-stream: {thread, message} for each new turn
    PATCH, DELETE                       405: nothing is removed

It is a plug, not an app: no pages of its own.
"""
from __future__ import annotations

import contextlib
import io
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import threads
from .util import ID_RE, Refuse

PREFIX = '/api/chat'


def list_threads(app):
    out = []
    for t in sorted(app.store.list_threads(), key=lambda t: t.get('opened_at') or ''):
        chat = threads.to_chat(app, t)
        out.append({'thread': t['id'], 'count': len(chat['messages']), 'last': chat['messages'][-1] if chat['messages']
                    else None, 'workspace': t.get('workspace'), 'state': t.get('state'), 'title': chat['title']})
    return {'threads': out}


def messages(app, tid, limit=200, before=None):
    chat = threads.to_chat(app, threads.read(app, tid))
    msgs = chat['messages']
    if before:
        idx = next((i for i, m in enumerate(msgs) if str(m['id']) == str(before)), None)
        if idx is not None:
            msgs = msgs[:idx]
    return {'messages': msgs[-max(1, min(int(limit), 1000)):]}


def post(app, key, text):
    """The person's turn: in a thread when `key` is a thread id, else a new message to the workspace `key`."""
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        if ID_RE.fullmatch(key):
            threads.thread(app, key, text, from_person=True)
            tid = key
        else:
            tid = threads.send(app, key, text)['id']
    chat = threads.to_chat(app, threads.read(app, tid))
    return chat['messages'][-1]


class Server:
    def __init__(self, app, host=None, port=None, token=None, allow_origin=None):
        s = app.cfg['serve']
        self.app = app
        self.host = host or s.get('host') or '127.0.0.1'
        self.port = int(port if port is not None else s.get('port') or 8765)
        self.token = token or s.get('token') or secrets.token_urlsafe(18)
        self.allow_origin = allow_origin or s.get('allow_origin')
        self.httpd = None

    def handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, code, body, ctype='application/json'):
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', ctype + ('; charset=utf-8' if 'json' in ctype else ''))
                self.send_header('Content-Length', str(len(data)))
                if server.allow_origin:
                    self.send_header('Access-Control-Allow-Origin', server.allow_origin)
                    self.send_header('Access-Control-Allow-Headers', 'authorization, content-type')
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self, query):
                auth = self.headers.get('Authorization') or ''
                given = auth[7:] if auth.lower().startswith('bearer ') else (query.get('token') or [''])[0]
                return secrets.compare_digest(given, server.token)

            def do_OPTIONS(self):  # noqa: N802
                self._send(204, b'', 'text/plain')

            def do_GET(self):  # noqa: N802
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if not self._authorized(q):
                    return self._send(401, {'error': 'token required'})
                try:
                    if url.path == '/api/events':
                        return self._events()
                    if url.path.rstrip('/') == PREFIX:
                        return self._send(200, list_threads(server.app))
                    if url.path.startswith(PREFIX + '/'):
                        tid = unquote(url.path[len(PREFIX) + 1:]).strip('/')
                        return self._send(200, messages(server.app, tid, (q.get('limit') or [200])[0],
                                                        (q.get('before') or [None])[0]))
                    return self._send(404, {'error': 'not found'})
                except Refuse as err:
                    return self._send(404, {'error': str(err)})

            def do_POST(self):  # noqa: N802
                url = urlparse(self.path)
                if not self._authorized(parse_qs(url.query)):
                    return self._send(401, {'error': 'token required'})
                if not url.path.startswith(PREFIX + '/'):
                    return self._send(404, {'error': 'not found'})
                try:
                    size = int(self.headers.get('Content-Length') or 0)
                    body = json.loads(self.rfile.read(size).decode('utf-8') or '{}')
                except ValueError:
                    return self._send(400, {'error': 'invalid JSON'})
                key = unquote(url.path[len(PREFIX) + 1:]).strip('/')
                try:
                    return self._send(200, post(server.app, key, body.get('text') or ''))
                except Refuse as err:
                    return self._send(409, {'error': str(err)})

            def do_PATCH(self):  # noqa: N802
                self._send(405, {'error': 'nothing is removed'})

            do_DELETE = do_PATCH  # noqa: N815

            def _events(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                if server.allow_origin:
                    self.send_header('Access-Control-Allow-Origin', server.allow_origin)
                self.end_headers()
                _, offset = server.app.events.read(0)
                try:
                    while True:
                        new, offset = server.app.events.read(offset)
                        for e in new:
                            if e.get('kind') == 'thread' and e.get('action') in ('open', 'turn', 'close'):
                                t = server.app.store.get_thread(e.get('id'))
                                if not t:
                                    continue
                                chat = threads.to_chat(server.app, t)
                                payload = {'thread': t['id'], 'state': t.get('state'),
                                           'message': chat['messages'][-1] if chat['messages'] else None}
                                self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode('utf-8'))
                        self.wfile.write(b': ping\n\n')
                        self.wfile.flush()
                        time.sleep(1)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        return Handler

    def start(self, background=False):
        self.httpd = ThreadingHTTPServer((self.host, self.port), self.handler())
        self.port = self.httpd.server_address[1]
        if background:
            threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def serve_forever(self):
        print(json.dumps({'url': f'http://{self.host}:{self.port}{PREFIX}', 'token': self.token}), flush=True)
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            pass

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
