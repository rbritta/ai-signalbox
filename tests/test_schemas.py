"""The JSON Schemas in docs/schemas describe the files the code writes: records made in a test world validate against
them (with a small checker of type, required, enum, const and pattern; no dependency)."""
import json
import re

from helpers import ROOT, Case

from ai_signalbox import offers
from ai_signalbox.util import touch

SCHEMAS = ROOT / 'docs' / 'schemas'
TYPES = {'string': str, 'integer': int, 'number': (int, float), 'boolean': bool, 'object': dict, 'array': list,
         'null': type(None)}


def problems(value, schema, where='$'):
    out = []
    if 'const' in schema and value != schema['const']:
        return [f'{where}: {value!r} is not {schema["const"]!r}']
    if 'type' in schema:
        kinds = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        ok = any(isinstance(value, TYPES[k]) and not (k in ('integer', 'number') and isinstance(value, bool))
                 for k in kinds)
        if not ok:
            return [f'{where}: {type(value).__name__} is not {kinds}']
    if 'enum' in schema and value not in schema['enum']:
        out.append(f'{where}: {value!r} not in {schema["enum"]}')
    if 'pattern' in schema and isinstance(value, str) and not re.search(schema['pattern'], value):
        out.append(f'{where}: {value!r} does not match {schema["pattern"]}')
    if isinstance(value, dict):
        out += [f'{where}: no {k}' for k in schema.get('required', []) if k not in value]
        for k, sub in (schema.get('properties') or {}).items():
            if k in value:
                out += problems(value[k], sub, f'{where}.{k}')
    if isinstance(value, list) and 'items' in schema:
        for i, item in enumerate(value):
            out += problems(item, schema['items'], f'{where}[{i}]')
    return out


class SchemaTest(Case):
    extra_toml = '[delivery]\nstart_touches = [0, 0, 0]\n[resources.app]\ndescription = "the app"\n'

    def schema(self, name):
        return json.loads((SCHEMAS / f'{name}.schema.json').read_text(encoding='utf-8'))

    def check(self, name, record):
        self.assertEqual(problems(record, self.schema(name)), [], f'{name}: {record}')

    def test_every_schema_is_well_formed(self):
        names = sorted(p.name for p in SCHEMAS.glob('*.schema.json'))
        self.assertEqual(names, [f'{n}.schema.json' for n in
                                 ('card', 'event', 'lease', 'message', 'offer', 'session', 'thread')])
        for p in SCHEMAS.glob('*.schema.json'):
            s = json.loads(p.read_text(encoding='utf-8'))
            self.assertEqual((s['type'], s['$schema'][:28]), ('object', 'https://json-schema.org/draf'))
            self.assertTrue(set(s['required']) <= set(s['properties']), p.name)

    def test_records_validate(self):
        s = self.session('ws1', name='alpha')
        touch(self.app.store.start_path('ws1', s['sid']), 'created')
        self.act_as(s)
        self.assertEqual(self.cli('note', '--item', '42', '--phase', 'plan')[0], 0)
        self.assertEqual(self.cli('hold', 'app', 'demo', '--minutes', '5')[0], 0)
        self.assertEqual(self.cli('send', 'ws1', '--text-file', self.write('m.txt', 'hello'))[0], 0)
        oid = json.loads(self.cli('offer', 'ws1', '--minutes', '30', '--item', '7', '--text-file',
                                  self.write('o.txt', 'work'))[1])['id']
        self.check('offer', self.app.store.offer_get(oid))
        self.assertEqual(self.cli('claim', oid)[0], 0)
        self.check('offer', self.app.store.offer_get(oid, offers.CLAIMED))
        self.check('card', self.card(s))
        self.check('lease', self.app.store.get_lease('app'))
        for _, m in self.app.store.box_list('ws1'):
            self.check('message', m)
        for m in self.app.store.delivered():
            self.check('message', m)
        for t in self.app.store.list_threads():
            self.check('thread', t)
        for e in self.app.events.all():
            self.check('event', e)
        reg = self.app.store.root / 'sessions'
        reg.mkdir(parents=True, exist_ok=True)
        self.assertEqual(self.cli('register', '--pid', str(self.script('bash')), '--workspace', 'ws2')[0], 0)
        for f in reg.glob('*.json'):
            self.check('session', json.loads(f.read_text(encoding='utf-8')))
