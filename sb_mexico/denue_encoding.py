"""Strict streaming preparation of DENUE CSVs, retaining original source identity."""
import codecs
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import tempfile
import unicodedata

READER_VERSION = 'denue_strict_mixed_v1'
_BAD = re.compile(b'[\x81\x8d\x8f\x90\x9d]')


class DenueEncodingError(ValueError):
    pass


def _stamp(stream):
    import os
    s = os.fstat(stream.fileno())
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def _unchanged(stream, path, stamp):
    try:
        s = path.stat()
        return _stamp(stream) == stamp == (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    except OSError:
        return False


def _scan(stream, block_size):
    decoders = {name: codecs.getincrementaldecoder(name)('strict')
                for name in ('utf-8-sig', 'cp1252')}
    digest = hashlib.sha256()
    size = 0
    stream.seek(0)
    for raw in iter(lambda: stream.read(block_size), b''):
        size += len(raw)
        digest.update(raw)
        for name, decoder in list(decoders.items()):
            try:
                decoder.decode(raw)
            except UnicodeDecodeError:
                del decoders[name]
    for name, decoder in list(decoders.items()):
        try:
            decoder.decode(b'', final=True)
        except UnicodeDecodeError:
            del decoders[name]
    if not size:
        raise DenueEncodingError('Empty DENUE source')
    return next(iter(decoders), None), digest.hexdigest()


def _candidate(data, bad, floor=0):
    for start in range(max(floor, bad-3), bad):
        lead = data[start]
        length = 2 if 0xC2 <= lead <= 0xDF else 3 if 0xE0 <= lead <= 0xEF else 4 if 0xF0 <= lead <= 0xF4 else 0
        end = start + length
        if not length or not start < bad < end or end > len(data):
            continue
        try:
            text = data[start:end].decode('utf-8', 'strict')
        except UnicodeDecodeError:
            continue
        if len(text) == 1 and not unicodedata.category(text).startswith('C'):
            return start, end, text
    return None


def _normalize(stream, out, events, path, block_size):
    stream.seek(0)
    carry = b''
    consumed = 0
    raw_hash = hashlib.sha256()
    normalized_hash = hashlib.sha256()
    count = 0
    offsets = []
    prefix = stream.read(3)
    if prefix == codecs.BOM_UTF8:
        raw_hash.update(prefix)
        consumed = 3
    else:
        stream.seek(0)
    def emit(text):
        encoded = text.encode('utf-8', 'strict')
        out.write(encoded)
        normalized_hash.update(encoded)
    while True:
        raw = stream.read(block_size)
        raw_hash.update(raw)
        data = carry + raw
        base = consumed - len(carry)
        consumed += len(raw)
        limit = len(data) if not raw else max(0, len(data)-3)
        # Keep a possible leading byte with an undefined byte in the tail.
        tail = _BAD.search(data, limit)
        if tail and raw:
            limit = min(limit, max(0, tail.start()-3))
        pos = 0
        while pos < limit:
            match = _BAD.search(data, pos, limit)
            if not match:
                emit(data[pos:limit].decode('cp1252', 'strict'))
                pos = limit
                break
            found = _candidate(data, match.start(), pos)
            if found is None:
                raise DenueEncodingError(f'{path}: unexplained mixed encoding at byte {base+match.start()} (0x{data[match.start()]:02x})')
            start, end, text = found
            emit(data[pos:start].decode('cp1252', 'strict'))
            emit(text)
            events.write(f'{base+start},{base+match.start()},{end-start}\n')
            if len(offsets) < 32:
                offsets.append(base+match.start())
            count += 1
            pos = end
        carry = data[pos:]
        if not raw:
            break
    return dict(raw_sha256=raw_hash.hexdigest(), normalized_sha256=normalized_hash.hexdigest(),
                repairs=count, repair_offsets=offsets, offsets_truncated=count > len(offsets))


@contextmanager
def prepared_denue(path, *, block_size=1024*1024):
    """Yield (binary stream, pandas encoding, audit); validate before any ingestion."""
    if block_size < 1:
        raise ValueError('DENUE block size must be positive')
    path = Path(path)
    with path.open('rb') as source:
        stamp = _stamp(source)
        has_bom = source.read(3) == codecs.BOM_UTF8
        try:
            encoding, raw_hash = _scan(source, block_size)
        except DenueEncodingError as error:
            raise DenueEncodingError(f'{path}: {error}') from error
        if not _unchanged(source, path, stamp):
            raise DenueEncodingError(f'{path}: source changed during validation')
        audit = dict(path=str(path.resolve()), reader_version=READER_VERSION,
                     raw_sha256=raw_hash, encoding=encoding or 'cp1252+utf8', repairs=0,
                     repair_offsets=[])
        if encoding and not (encoding == 'cp1252' and has_bom):
            source.seek(0)
            yield source, encoding, audit
        else:
            with tempfile.TemporaryDirectory(prefix='denue-encoding-') as temporary:
                normalized = Path(temporary)/'normalized.csv'
                with normalized.open('wb') as out, (Path(temporary)/'repairs.csv').open('w', encoding='ascii') as events:
                    audit.update(_normalize(source, out, events, path, block_size))
                if audit['raw_sha256'] != raw_hash or not _unchanged(source, path, stamp):
                    raise DenueEncodingError(f'{path}: source changed during normalization')
                with normalized.open('rb') as stream:
                    yield stream, 'utf-8-sig', audit
        if not _unchanged(source, path, stamp) or _scan(source, block_size)[1] != raw_hash:
            raise DenueEncodingError(f'{path}: source changed during ingestion')
