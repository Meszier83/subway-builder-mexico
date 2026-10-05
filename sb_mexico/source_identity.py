"""Record identity checks independent of coordinates and input chunk boundaries."""
import hashlib

import pandas as pd


class RecordIdentityLedger:
    """Keep only compact identity/fingerprint hashes while streaming source rows.

    Missing identities are retained and counted. Conflicts are fatal: file order
    cannot select a source vintage. Fingerprints contain normalized fields used
    by demand, rather than incidental CSV headers or column order.
    """
    def __init__(self, source):
        self.source = source
        self.seen = {}
        self.report = dict(source=source, duplicates_removed=0, missing_identity=0)

    @staticmethod
    def clean(value):
        if pd.isna(value):
            return ''
        text = str(value).strip().upper()
        return '' if text in ('', 'NAN', 'NONE', 'N/D', '-1', '0') else text

    @staticmethod
    def digest(value):
        return hashlib.sha256(repr(value).encode('utf-8')).digest()

    def keep(self, aliases, payload):
        aliases = [self.digest(alias) for alias in aliases]
        if not aliases:
            self.report['missing_identity'] += 1
            return True
        fingerprint = self.digest(payload)
        duplicate = False
        for alias in aliases:
            previous = self.seen.get(alias)
            if previous is not None:
                if previous != fingerprint:
                    raise ValueError(f'Conflicting {self.source} records for the same official identity')
                duplicate = True
        for alias in aliases:
            self.seen[alias] = fingerprint
        if duplicate:
            self.report['duplicates_removed'] += 1
        return not duplicate
