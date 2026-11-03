"""Behavior checks for the local-calendar WakaTime README updater."""
from datetime import datetime, timezone
from http.client import HTTPException
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'update_wakatime.py'
sys.path.insert(0, str(SCRIPT.parent))
START = '<!--START_SECTION:waka-->'
END = '<!--END_SECTION:waka-->'
ORIGINAL = f'个人介绍\r\n{START}\r\nold\r\n{END}\r\nmanual footer\r\n'.encode()


def payload(total=3600, languages=None):
    return {'data': [{'grand_total': {'total_seconds': total}, 'languages':
                     languages if languages is not None else [{'name': 'Python', 'total_seconds': total}]}]}


class UpdateWakatimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.readme = Path(self.temp.name) / 'README.md'
        self.readme.write_bytes(ORIGINAL)

    def module(self):
        self.assertTrue(SCRIPT.exists(), 'WakaTime updater must exist')
        import update_wakatime
        return update_wakatime

    def run_cli(self, data):
        fixture = Path(self.temp.name) / 'summary.json'
        fixture.write_text(json.dumps(data), encoding='utf-8')
        return subprocess.run([sys.executable, str(SCRIPT), '--readme', str(self.readme),
                               '--data-file', str(fixture)], capture_output=True, text=True)

    def test_calendar_window_includes_today_in_shanghai_across_year_boundary(self):
        module = self.module()
        start, end = module.date_range(datetime(2026, 12, 31, 17, tzinfo=timezone.utc))
        self.assertEqual((start.isoformat(), end.isoformat()), ('2026-12-26', '2027-01-01'))

    def test_aggregates_days_and_sorts_ties_by_name(self):
        module = self.module()
        data = payload(200, [{'name': 'Zig', 'total_seconds': 100}, {'name': 'C', 'total_seconds': 100}])
        data['data'].append(payload(100, [{'name': 'Python', 'total_seconds': 100}])['data'][0])
        self.assertEqual(module.aggregate(data), (300, [('C', 100), ('Python', 100), ('Zig', 100)]))

    def test_top_five_percentages_include_other_and_omitted_languages(self):
        module = self.module()
        names = ['A', 'B', 'C', 'D', 'Other', 'Z']
        data = payload(1000, [{'name': name, 'total_seconds': seconds} for name, seconds in
                              zip(names, [300, 200, 150, 100, 200, 50])])
        start, end = module.date_range(datetime(2026, 11, 3, tzinfo=timezone.utc))
        text = module.render(data, start, end)
        self.assertIn('Last 7 days (including today)', text)
        self.assertIn('2026-10-28 - 2026-11-03', text)
        self.assertIn('Total Time: 16 mins', text)
        self.assertIn('30.00%', text)
        self.assertIn('Other', text)
        self.assertNotIn('\nZ ', text)
        self.assertEqual(sum(line.endswith('%') for line in text.splitlines()), 5)

    def test_zero_activity_is_honest(self):
        for data in [payload(0, []), {'data': []}]:
            with self.subTest(data=data):
                result = self.run_cli(data)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('No activity tracked in this period.', self.readme.read_text())
                self.assertNotIn('Python', self.readme.read_text())

    def test_preserves_manual_bytes_and_is_idempotent(self):
        result = self.run_cli(payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = self.readme.read_bytes()
        self.assertTrue(updated.startswith(f'个人介绍\r\n{START}\r\n```txt\r\n'.encode()))
        self.assertTrue(updated.endswith(f'```\r\n{END}\r\nmanual footer\r\n'.encode()))
        before = self.readme.stat().st_mtime_ns
        self.assertEqual(self.run_cli(payload()).returncode, 0)
        self.assertEqual(self.readme.stat().st_mtime_ns, before)

    def test_bad_markers_never_write(self):
        for text in ['missing', START, END + START, START + START + END, START + END + END]:
            with self.subTest(text=text):
                self.readme.write_bytes(text.encode())
                self.assertNotEqual(self.run_cli(payload()).returncode, 0)
                self.assertEqual(self.readme.read_bytes(), text.encode())

    def test_invalid_payload_never_writes(self):
        cases = [None, {}, {'data': None}, {'data': [{}]}, payload(-1), payload(float('nan')),
                 payload(True), payload(1, [{'name': 'Python'}]), payload(1, None),
                 payload(1, [{'name': 'bad\n```', 'total_seconds': 1}]),
                 payload(1, [{'name': 'Python', 'total_seconds': '1'}])]
        cases[8] = {'data': [{'grand_total': {'total_seconds': 1}, 'languages': None}]}
        for data in cases:
            with self.subTest(data=data):
                result = self.run_cli(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.readme.read_bytes(), ORIGINAL)

    def test_live_request_uses_explicit_calendar_dates_and_bearer(self):
        module = self.module()
        observed = {}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps(payload()).encode()
        def open_request(request, timeout):
            observed.update(url=request.full_url, headers=dict(request.header_items()), timeout=timeout)
            return Response()
        with patch.object(module, 'urlopen', open_request):
            start, end = module.date_range(datetime(2026, 11, 3, tzinfo=timezone.utc))
            self.assertEqual(module.fetch(start, end, 'secret'), payload())
        self.assertEqual(parse_qs(urlparse(observed['url']).query),
                         {'start': ['2026-10-28'], 'end': ['2026-11-03'], 'timezone': ['Asia/Shanghai']})
        self.assertEqual(observed['headers']['Authorization'], 'Bearer secret')
        self.assertEqual(observed['timeout'], 20)

    def test_network_auth_pending_and_json_errors_are_safe_and_do_not_write(self):
        module = self.module()
        class Response:
            def __init__(self, status, body): self.status, self.body = status, body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return self.body
        cases = [HTTPError('https://example.com/secret', 401, 'secret', None, None),
                 HTTPError('https://example.com/secret', 403, 'secret', None, None),
                 URLError('secret'), TimeoutError('secret'), HTTPException('secret'),
                 Response(202, b'secret'), Response(200, b'secret'), Response(200, b'{"data":null}')]
        for response in cases:
            with self.subTest(response=type(response).__name__):
                def opener(*args, **kwargs):
                    if isinstance(response, Exception): raise response
                    return response
                stderr = io.StringIO()
                with patch.object(module, 'urlopen', opener), patch.dict('os.environ', {'WAKATIME_TOKEN': 'secret'}), contextlib.redirect_stderr(stderr):
                    self.assertNotEqual(module.main(['--readme', str(self.readme)]), 0)
                self.assertNotIn('secret', stderr.getvalue())
                if isinstance(response, HTTPError):
                    self.assertIn(f'HTTP {response.code}', stderr.getvalue())
                self.assertEqual(self.readme.read_bytes(), ORIGINAL)

    def test_missing_token_leaves_readme_untouched(self):
        module = self.module()
        with patch.dict('os.environ', {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(module.main(['--readme', str(self.readme)]), 0)
        self.assertEqual(self.readme.read_bytes(), ORIGINAL)


if __name__ == '__main__':
    unittest.main()
