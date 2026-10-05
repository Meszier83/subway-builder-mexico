"""Production HTTP framing/identity/assets and safe listener ownership."""
import hashlib
import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from tools import wizard


class WizardStartupTests(unittest.TestCase):
    def setUp(self):
        self.server = wizard.WizardHTTPServer(('127.0.0.1', 0), wizard.WizardRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def read(self, path, method='GET'):
        self.client.request(method, path, headers={'Connection':'keep-alive'})
        response = self.client.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_complete_page_assets_and_reused_connection(self):
        for _ in range(3):
            status, headers, body = self.read('/')
            self.assertEqual(status, 200)
            self.assertTrue(body.rstrip().endswith(b'</html>'))
            self.assertIn(b'/static/wizard/app.js?v=', body)
            self.assertNotIn(b'cdn.tailwindcss.com', body)
            self.assertIn('X-Wizard-Instance', headers)
            status, headers, body = self.read('/static/wizard/app.js')
            expected = Path(wizard.STATIC_DIR, 'app.js').read_bytes()
            self.assertEqual(status, 200)
            self.assertEqual(hashlib.sha256(body).digest(), hashlib.sha256(expected).digest())
            self.assertIn('javascript', headers['Content-Type'])
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            status, headers, body = self.read('/api/instance')
            identity = json.loads(body)
            self.assertEqual(identity['instance_id'], headers['X-Wizard-Instance'])
            self.assertEqual(identity['port'], self.server.server_port)
            self.assertEqual(identity['code_sha256'], wizard.INSTANCE_CODE_SHA256)
            self.assertEqual(self.client.sock.getpeername()[1], self.server.server_port)

    def test_options_and_missing_assets_are_framed_and_traversal_rejected(self):
        status, headers, body = self.read('/', 'OPTIONS')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Length'], '0')
        self.assertEqual(body, b'')
        for path in ['/static/wizard/missing.js', '/static/wizard/%2e%2e/%2e%2e/wizard.py',
                     '/static/wizard/C:%5cWindows%5cwin.ini', '/static/wizard/%00.js']:
            status, headers, body = self.read(path)
            self.assertEqual(status, 404)
            self.assertIn('error', json.loads(body))

    def test_duplicate_listener_is_rejected(self):
        with self.assertRaises(OSError):
            wizard.WizardHTTPServer(('127.0.0.1', self.server.server_port), wizard.WizardRequestHandler)

    def test_health_is_nonblocking_cached_and_runs_one_read_only_probe(self):
        release = threading.Event()
        finished = threading.Event()
        def probe():
            release.wait(timeout=2)
            finished.set()
            return {'status':'ok', 'wsl_ready':False, 'checked_at':wizard.time.time()}
        with patch.object(wizard, 'system_health_cache', None), \
                patch.object(wizard, 'system_health_running', False), \
                patch.object(wizard, 'probe_system_health', side_effect=probe) as inspected:
            for _ in range(10):
                self.assertEqual(wizard.get_system_health()['status'], 'checking')
            self.assertEqual(inspected.call_count, 1)
            release.set()
            self.assertTrue(finished.wait(timeout=2))
            # The worker may still be acquiring the cache lock after releasing the event.
            for _ in range(100):
                result = wizard.get_system_health()
                if result['status'] == 'ok':
                    break
                threading.Event().wait(.01)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(inspected.call_count, 1)

    def test_windows_health_probe_is_bounded_and_never_restarts_wsl(self):
        with patch.object(wizard.sys, 'platform', 'win32'), \
                patch.object(wizard.subprocess, 'run') as launched:
            launched.return_value.returncode = 0
            launched.return_value.stdout = '{"tippecanoe":true,"depot":true}'
            result = wizard.probe_system_health()
            self.assertTrue(result['wsl_ready'])
            self.assertEqual(launched.call_count, 1)
            self.assertEqual(launched.call_args.kwargs['timeout'], 10)
            self.assertNotIn('--shutdown', launched.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
