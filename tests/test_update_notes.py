"""Exercise the updater CLI against temporary feeds and README files."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'update_notes.py'
START = '<!-- notes:start -->'
END = '<!-- notes:end -->'
ORIGINAL = f'介绍\r\n{START}\r\nold content\r\n{END}\r\nfooter\r\n'


def feed(items):
    return '<rss version="2.0"><channel>' + ''.join(
        f'<item><title>{escape(title)}</title><link>{escape(url)}</link></item>'
        for title, url in items
    ) + '</channel></rss>'


class UpdateNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.readme = Path(self.temp.name) / 'README.md'
        self.feed = Path(self.temp.name) / 'feed.xml'
        self.readme.write_bytes(ORIGINAL.encode())

    def run_updater(self, xml):
        self.feed.write_text(xml, encoding='utf-8')
        return subprocess.run(
            [sys.executable, str(SCRIPT), '--feed-file', str(self.feed),
             '--readme', str(self.readme)], capture_output=True, text=True,
        )

    def test_replaces_only_marker_contents_and_preserves_newlines(self):
        result = self.run_updater(feed([('第一篇', 'https://example.com/one')]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.readme.read_bytes(),
                         f'介绍\r\n{START}\r\n- [第一篇](https://example.com/one)\r\n{END}\r\nfooter\r\n'.encode())

    def test_unchanged_output_is_not_rewritten(self):
        xml = feed([('One', 'https://example.com/one')])
        self.assertEqual(self.run_updater(xml).returncode, 0)
        previous = self.readme.stat().st_mtime_ns
        self.assertEqual(self.run_updater(xml).returncode, 0)
        self.assertEqual(self.readme.stat().st_mtime_ns, previous)

    def test_uses_first_three_valid_unique_links_in_source_order(self):
        result = self.run_updater(feed([
            ('Bad', 'javascript:alert(1)'), ('', 'https://example.com/empty'),
            ('One', 'https://example.com/1'), ('Duplicate', 'https://example.com/1'),
            ('Two', 'https://example.com/2'), ('Three', 'https://example.com/3'),
            ('Four', 'https://example.com/4'),
        ]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.readme.read_bytes(),
                         f'介绍\r\n{START}\r\n- [One](https://example.com/1)\r\n- [Two](https://example.com/2)\r\n- [Three](https://example.com/3)\r\n{END}\r\nfooter\r\n'.encode())

    def test_escapes_title_and_encodes_link(self):
        result = self.run_updater(feed([
            ('A [link] *bold* <img> & \\ code`\nnext', 'https://example.com/a (b)/中文?q=x y&z=1')
        ]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('- [A \\[link\\] \\*bold\\* &lt;img&gt; &amp; \\\\ code\\` next](https://example.com/a%20%28b%29/%E4%B8%AD%E6%96%87?q=x%20y&z=1)', self.readme.read_text())

    def test_empty_malformed_and_non_rss_feeds_leave_readme_untouched(self):
        for xml in ['', '<rss>', '<rss><channel /></rss>', '<html />',
                    feed([('Bad', 'file:///etc/passwd'), ('Bad', 'https:///missing-host')])]:
            with self.subTest(xml=xml):
                result = self.run_updater(xml)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.readme.read_bytes(), ORIGINAL.encode())

    def test_missing_duplicate_or_reversed_markers_leave_readme_untouched(self):
        for text in ['hello', START, END + START, START + START + END,
                     START + END + END]:
            with self.subTest(text=text):
                self.readme.write_text(text)
                result = self.run_updater(feed([('One', 'https://example.com/1')]))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.readme.read_text(), text)

    def test_missing_feed_file_leaves_readme_untouched(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), '--feed-file', str(self.feed),
             '--readme', str(self.readme)], capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.readme.read_bytes(), ORIGINAL.encode())


if __name__ == '__main__':
    unittest.main()
