import copy
import gzip
import io
import json
import unittest
import zipfile

from tools.map_analyzer import AnalyzerHandler, load_map, normalize


def fixture():
    return dict(points=[dict(id="home", location=[-86.85, 21.1], residents=130, jobs=0),
                        dict(id="work", location=[-86.84, 21.11], residents=0, jobs=130)],
                pops=[dict(id="a", residenceId="home", jobId="work", size=100),
                      dict(id="b", residenceId="home", jobId="work", size=30)])


def package(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, json.dumps(content))
    stream.seek(0)
    return stream


class MapAnalyzerImportTests(unittest.TestCase):
    def test_json_import_retains_people_and_trip_weights_without_mutation(self):
        data = fixture()
        original = copy.deepcopy(data)
        result = load_map(io.BytesIO(json.dumps(data).encode()))
        self.assertEqual(result["points"], data["points"])
        self.assertEqual(sum(p["size"] for p in result["pops"]), 130)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(data, original)

    def test_nested_zip_uses_matching_config_ignores_unrelated_members(self):
        result = load_map(package({"CUR/demand_data.json": fixture(),
                                   "CUR/config.json": {"name": "Cancún <test>"},
                                   "config.json": {"name": "Wrong city"}, "unused.json": {}}))
        self.assertEqual(result["name"], "Cancún <test>")
        self.assertEqual(sum(p["residents"] for p in result["points"]), 130)

    def test_multiple_maps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            load_map(package({"a/demand_data.json": fixture(), "b/demand_data.json": fixture()}))

    def test_mismatched_counts_are_reported_without_repair(self):
        data = fixture()
        data["points"][1]["jobs"] = 999
        result = normalize(data)
        self.assertIn("1 points", result["warnings"][0])
        self.assertEqual(result["points"][1]["jobs"], 999)
        self.assertEqual(sum(p["size"] for p in result["pops"]), 130)

    def test_missing_trips_and_explicit_empty_trips_are_distinguished(self):
        data = fixture()
        del data["pops"]
        result = normalize(data)
        self.assertFalse(result["tripsAvailable"])
        self.assertIn("unavailable", result["warnings"][0])
        data["pops"] = []
        result = normalize(data)
        self.assertTrue(result["tripsAvailable"])
        self.assertIn("differ", result["warnings"][0])

    def test_invalid_counts_fail_instead_of_being_omitted(self):
        for bad in [-1, 1.2, float("nan"), float("inf"), True, "30", 10**1000, None]:
            with self.subTest(bad=str(bad)[:30]):
                data = fixture()
                data["points"][0]["residents"] = bad
                with self.assertRaises(ValueError):
                    normalize(data)

    def test_dangling_trip_reference_fails(self):
        data = fixture()
        data["pops"][0]["jobId"] = "unknown"
        with self.assertRaisesRegex(ValueError, "missing point"):
            normalize(data)

    def test_duplicate_point_identity_fails(self):
        data = fixture()
        data["points"][1]["id"] = "home"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            normalize(data)

    def test_invalid_locations_fail(self):
        for location in [[21, -186], [181, 21], [0, 86], [0], [False, 20], [0, float("inf")]]:
            data = fixture()
            data["points"][0]["location"] = location
            with self.subTest(location=location), self.assertRaises(ValueError):
                normalize(data)

    def test_invalid_trip_sizes_fail(self):
        for size in [0, -2, True, 1.5]:
            data = fixture()
            data["pops"][0]["size"] = size
            with self.subTest(size=size), self.assertRaises(ValueError):
                normalize(data)

    def test_empty_and_non_demand_inputs_fail(self):
        for data in [[], {}, {"points": []}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                normalize(data)

    def test_nonstandard_json_numbers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid JSON number"):
            load_map(io.BytesIO(b'{"points":[],"bad":NaN}'))


class MapAnalyzerResponseTests(unittest.TestCase):
    def test_compressed_assets_are_complete_and_negotiate_identity(self):
        # Exercise the actual response method without relying on local sockets.
        class Response:
            reply = AnalyzerHandler.reply
            def __init__(self, accept):
                self.headers = {"Accept-Encoding": accept}
                self.sent = {}
                self.wfile = io.BytesIO()
            def send_response(self, status): self.status = status
            def send_header(self, name, value): self.sent[name] = value
            def end_headers(self): pass
        asset = b"Leaflet response content " * 8000
        for accept, compressed in [("gzip, deflate, br", True), ("", False), ("gzip;q=0", False)]:
            with self.subTest(accept=accept):
                response = Response(accept)
                response.reply(200, asset, "text/javascript; charset=utf-8")
                body = response.wfile.getvalue()
                self.assertEqual(int(response.sent["Content-Length"]), len(body))
                self.assertEqual(response.sent["Vary"], "Accept-Encoding")
                if compressed:
                    self.assertEqual(response.sent["Content-Encoding"], "gzip")
                    self.assertLess(len(body), len(asset))
                    self.assertEqual(gzip.decompress(body), asset)
                else:
                    self.assertNotIn("Content-Encoding", response.sent)
                    self.assertEqual(body, asset)


class MapAnalyzerPartsTests(unittest.TestCase):
    def test_production_part_endpoints_reassemble_and_release(self):
        from types import SimpleNamespace
        from tools.map_analyzer import IMPORT_RESULTS, RESPONSE_PART
        class Handler(AnalyzerHandler):
            def __init__(self):
                self.server=SimpleNamespace(server_port=5052)
                self.headers={'Origin':'http://127.0.0.1:5052','X-Map-Analyzer':'1'}
            def reply(self,status,body,content_type=None):
                self.status,self.body=status,body
        handler=Handler()
        data=fixture()
        data['points'][0]['id']='home'+'é'*12000
        for pop in data['pops']: pop['residenceId']=data['points'][0]['id']
        raw=json.dumps(data).encode()
        handler.path='/api/import?response=parts'
        handler.headers['Content-Length']=str(len(raw))
        handler.rfile=io.BytesIO(raw)
        handler.do_POST()
        self.assertEqual(handler.status,200)
        manifest=json.loads(handler.body)
        token=manifest['token']
        try:
            received=[]
            for index in range((manifest['bytes']+RESPONSE_PART-1)//RESPONSE_PART):
                handler.path=f'/api/import-result/{token}/{index}'
                handler.do_GET()
                self.assertEqual(handler.status,200)
                self.assertLessEqual(len(handler.body),RESPONSE_PART)
                received.append(handler.body)
            self.assertEqual(json.loads(b''.join(received)),normalize(data))
            handler.path=f'/api/import-result/{token}'
            handler.do_DELETE()
            self.assertNotIn(token,IMPORT_RESULTS)
        finally:
            IMPORT_RESULTS.pop(token,None)


if __name__ == "__main__":
    unittest.main()
