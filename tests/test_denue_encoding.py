import codecs
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sb_mexico.denue_encoding import prepared_denue, DenueEncodingError


class DenueEncodingTests(unittest.TestCase):
    def decode(self, raw, block_size=1024):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'source.csv'
            path.write_bytes(raw)
            with prepared_denue(path, block_size=block_size) as (stream, encoding, audit):
                result = stream.read().decode(encoding)
                info = dict(audit)
            self.assertEqual(path.read_bytes(), raw)
            return result, info

    def test_valid_encodings_and_bom(self):
        text = 'id,nombre\r\n001,Mecánico “Sol” 20°\r\n'
        for encoding in ('utf-8', 'utf-8-sig', 'cp1252'):
            with self.subTest(encoding=encoding):
                actual, audit = self.decode(text.encode(encoding), 1)
                self.assertEqual(actual, text)
                self.assertEqual(audit['repairs'], 0)

    def test_late_mixed_sequence_all_boundaries(self):
        prefix = ('id,nombre\n001,Mecánico\n'.encode('cp1252') + b' ' * 65536)
        raw = prefix + b'GRANJAS DEL M\xc3\x81RQUEZ\r\n'
        expected = prefix.decode('cp1252') + 'GRANJAS DEL MÁRQUEZ\r\n'
        for block in (1, 2, 3, 4, 7, 1024, 65536):
            with self.subTest(block=block):
                actual, audit = self.decode(raw, block)
                self.assertEqual(actual, expected)
                self.assertEqual(audit['repairs'], 1)
                self.assertEqual(audit['repair_offsets'], [raw.index(b'\x81')])

    def test_split_valid_utf8_is_not_cp1252(self):
        raw = b'a' * 65535 + 'Á'.encode('utf-8')
        actual, audit = self.decode(raw, 65536)
        self.assertEqual(actual, 'a' * 65535 + 'Á')
        self.assertEqual(audit['encoding'], 'utf-8-sig')

    def test_two_three_four_byte_recovery_at_boundaries(self):
        for text in ('Ё', '亁', '🎁'):
            raw=b'Mec\xe1nico '+text.encode('utf-8')+b' end'
            expected='Mecánico '+text+' end'
            for block in range(1,9):
                with self.subTest(text=text,block=block):
                    actual,audit=self.decode(raw,block)
                    self.assertEqual(actual,expected)
                    self.assertEqual(audit['repairs'],1)

    def test_only_blocking_sequences_are_repaired(self):
        raw = b'Mec\xe1nico \x93sol\x94 \xc3\xb1 M\xc3\x81RQUEZ'
        actual, _ = self.decode(raw, 2)
        self.assertEqual(actual, 'Mecánico “sol” Ã± MÁRQUEZ')

    def test_mixed_bom_is_stripped_once(self):
        actual, _ = self.decode(codecs.BOM_UTF8 + b'id,nombre\n1,Mec\xe1nico M\xc3\x81RQUEZ', 1)
        self.assertTrue(actual.startswith('id,nombre'))
        self.assertIn('Mecánico MÁRQUEZ', actual)

    def test_unexplained_and_invalid_sequences_fail(self):
        for suffix in (b'\x81', b'\xe0\x81\x81', b'\xe2\x81', b'\xc2\x81'):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(DenueEncodingError, 'source.csv.*byte'):
                    self.decode(b'Mec\xe1nico '+suffix, 1)

    def test_source_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'source.csv';p.write_bytes(b'id,name\n1,Test\n')
            with self.assertRaisesRegex(DenueEncodingError, 'changed'):
                with prepared_denue(p) as (stream, _, _):
                    stream.read()
                    with p.open('ab') as out:out.write(b'2,New\n')

    def test_temporary_cleanup_after_consumer_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'source.csv';p.write_bytes(b'Mec\xe1nico M\xc3\x81RQUEZ')
            with self.assertRaisesRegex(RuntimeError, 'consumer'):
                with prepared_denue(p) as (stream, _, _):
                    normalized=Path(stream.name)
                    self.assertTrue(normalized.exists())
                    raise RuntimeError('consumer')
            self.assertFalse(normalized.exists())


class DenueLoaderEncodingTests(unittest.TestCase):
    def test_quoted_fields_and_parser_failure_keep_source_context(self):
        from sb_mexico.inegi import load_denue
        bbox=dict(min_lon=-100,min_lat=18,max_lon=-98,max_lat=20)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'quoted.csv'
            header=b'id;nom_estab;latitud;longitud;cve_ent;cve_mun;per_ocu\n'
            p.write_bytes(header+b'001;"Mec\xe1nico;\nM\xc3\x81RQUEZ";19;-99;09;001;0 a 5 personas\n')
            frame=load_denue(str(p),bbox)
            self.assertEqual(frame.iloc[0]['id'],'001')
            self.assertEqual(frame.iloc[0].nom_estab,'Mecánico;\nMÁRQUEZ')
            p.write_bytes(header+b'001;"unterminated')
            with self.assertRaisesRegex(ValueError,'quoted.csv'):
                load_denue(str(p),bbox)

    def test_demand_evidence_identity_tracks_both_reader_files(self):
        from types import SimpleNamespace
        from sb_mexico.demand_v2.request import evidence_identity
        from sb_mexico.residential_employment import file_sha256
        request=SimpleNamespace(config={'city':{'bbox':[-100,18,-98,20]}},source_metadata={})
        baseline=evidence_identity(request,[])
        for filename in ('inegi.py','denue_encoding.py'):
            def revised(path):
                return 'new-reader-revision' if Path(path).name==filename else file_sha256(path)
            with patch('sb_mexico.residential_employment.file_sha256',side_effect=revised):
                self.assertNotEqual(evidence_identity(request,[]),baseline)

    def test_late_record_preserves_totals_and_full_scope(self):
        from sb_mexico.inegi import load_denue
        bbox=dict(min_lon=-100,min_lat=18,max_lon=-98,max_lat=20)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'mixed.csv'
            header=b'id,nom_estab,latitud,longitud,cve_ent,cve_mun,per_ocu\n'
            with p.open('wb') as out:
                out.write(header)
                for i in range(100001):
                    out.write(f'{i},Mecánico,19,-99,09,001,0 a 5 personas\n'.encode('cp1252'))
                out.write(b'last,GRANJAS DEL M\xc3\x81RQUEZ,16,-99,12,001,251 y m\xe1s personas\n')
            full=load_denue([str(p),str(p)],bbox,full_scope=True)
            self.assertEqual(len(full),100002)
            self.assertEqual(full.iloc[-1].nom_estab,'GRANJAS DEL MÁRQUEZ')
            self.assertEqual(full.attrs['mun_totals_global']['12001'],450.)
            self.assertEqual(full.attrs['denue_decoding'][0]['repairs'],1)
            clipped=load_denue(str(p),bbox)
            self.assertEqual(len(clipped),100001)
            self.assertEqual(clipped.attrs['mun_totals_global']['12001'],450.)

    def test_missing_selected_source_is_not_silently_omitted(self):
        from sb_mexico.inegi import load_denue
        with self.assertRaises(FileNotFoundError):
            load_denue('/missing/selected/denue.csv',{})


if __name__=='__main__':unittest.main()
