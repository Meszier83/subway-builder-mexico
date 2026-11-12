"""Bounded storage and JSON streaming for the building exporters."""
import contextvars
import functools
import json
import mmap
import pickle
import sqlite3
import tempfile
from pathlib import Path

_active = contextvars.ContextVar('building_workspace')


def workspace(fn):
    @functools.wraps(fn)
    def wrapped(self, input_path, *args, **kwargs):
        with tempfile.TemporaryDirectory(prefix='building-index-', dir=Path(input_path).parent) as root:
            resources = []
            token = _active.set((Path(root), resources))
            try:
                return fn(self, input_path, *args, **kwargs)
            finally:
                for resource in reversed(resources):
                    resource.close()
                _active.reset(token)
    return wrapped


def connection():
    root, resources = _active.get()
    con = sqlite3.connect(root / f'{len(resources)}.sqlite')
    con.execute('PRAGMA cache_size=-4096')
    con.execute('PRAGMA temp_store=FILE')
    resources.append(con)
    return con


class DiskList:
    def __init__(self, values=()):
        self.con = connection()
        self.con.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, value BLOB)')
        self.count = 0
        for value in values:
            self.append(value)

    def append(self, value):
        self.con.execute('INSERT INTO records VALUES (?, ?)', (self.count, pickle.dumps(value)))
        self.count += 1

    def __len__(self):
        return self.count

    def __iter__(self):
        for value, in self.con.execute('SELECT value FROM records ORDER BY id'):
            yield pickle.loads(value)


class CellAppender:
    __slots__ = ('owner', 'key')

    def __init__(self, owner, key):
        self.owner, self.key = owner, key

    def append(self, value):
        self.owner.con.execute('INSERT INTO refs VALUES (?,?,?)', (*self.key,value))


class DiskCells:
    def __init__(self):
        self.con = connection()
        self.con.execute('CREATE TABLE refs (x INTEGER, y INTEGER, id INTEGER)')
        self.con.execute('CREATE INDEX cell ON refs(y,x)')

    def setdefault(self, key, default):
        return CellAppender(self,key)

    def items(self, sorted_cells=False):
        order = 'y,x' if sorted_cells else 'MIN(rowid)'
        for x, y in self.con.execute(f'SELECT x,y FROM refs GROUP BY y,x ORDER BY {order}'):
            yield (x, y), [r[0] for r in self.con.execute(
                'SELECT id FROM refs WHERE y=? AND x=? ORDER BY rowid', (y,x))]


def disk_buffer(size):
    root, resources = _active.get()
    f = open(root / 'buffer.bin', 'w+b')
    resources.append(f)
    f.truncate(size)
    buffer = mmap.mmap(f.fileno(), size)
    resources.append(buffer)
    return buffer


def dump_index(value, out):
    out.write('{')
    for n, (key, data) in enumerate(value.items()):
        if n:
            out.write(',')
        out.write(json.dumps(key) + ':')
        if key in ('cells', 'buildings'):
            out.write('[')
            for i, item in enumerate(data):
                if i:
                    out.write(',')
                if key == 'buildings':
                    item = {k:v for k,v in item.items() if k != 'center'}
                json.dump(item, out, separators=(',', ':'))
            out.write(']')
        else:
            json.dump(data, out, separators=(',', ':'))
    out.write('}')


def iter_features(path):
    """Read our FeatureCollection writer's features with at most one feature buffered."""
    decoder = json.JSONDecoder()
    with open(path, encoding='utf-8') as src:
        prefix = '{"type":"FeatureCollection","features":['
        if src.read(len(prefix)) != prefix:
            raise ValueError('Expected the bounded building FeatureCollection format')
        buffer = ''
        eof = False
        while True:
            buffer = buffer.lstrip()
            if buffer.startswith(','):
                buffer = buffer[1:].lstrip()
            if buffer.startswith(']'):
                if (buffer + src.read()).strip() != ']}':
                    raise ValueError('Invalid FeatureCollection suffix')
                return
            try:
                item, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                if eof:
                    raise ValueError('Truncated building FeatureCollection')
                chunk = src.read(65536)
                eof = not chunk
                buffer += chunk
                if len(buffer) > 64 * 1024 * 1024:
                    raise ValueError('Single building exceeds 64 MiB JSON budget')
                continue
            yield item
            buffer = buffer[end:]


def write_features(path, features):
    with open(path, 'w', encoding='utf-8', newline='\n') as out:
        out.write('{"type":"FeatureCollection","features":[')
        for i, feature in enumerate(features):
            if i:
                out.write(',')
            json.dump(feature, out, separators=(',', ':'), allow_nan=False)
        out.write(']}')
