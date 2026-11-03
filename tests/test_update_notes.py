"""Exercise the updater CLI against temporary feeds and README files."""
from pathlib import Path
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from xml.sax.saxutils import escape

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'update_notes.py'
sys.path.insert(0, str(SCRIPT.parent))
import update_notes

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
                         f'介绍\r\n{START}\r\n- `#01` [第一篇](https://example.com/one)\r\n{END}\r\nfooter\r\n'.encode())

    def test_unchanged_output_is_not_rewritten(self):
        xml = feed([('One', 'https://example.com/one')])
        self.assertEqual(self.run_updater(xml).returncode, 0)
        previous = self.readme.stat().st_mtime_ns
        self.assertEqual(self.run_updater(xml).returncode, 0)
        self.assertEqual(self.readme.stat().st_mtime_ns, previous)

    def test_uses_first_five_valid_unique_links_in_source_order(self):
        result = self.run_updater(feed([
            ('Bad', 'javascript:alert(1)'), ('', 'https://example.com/empty'),
            ('One', 'https://example.com/1'), ('Duplicate', 'https://example.com/1'),
            ('Two', 'https://example.com/2'), ('Three', 'https://example.com/3'),
            ('Four', 'https://example.com/4'), ('Five', 'https://example.com/5'),
            ('Six', 'https://example.com/6'),
        ]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.readme.read_bytes(),
                         f'介绍\r\n{START}\r\n- `#01` [One](https://example.com/1)\r\n- `#02` [Two](https://example.com/2)\r\n- `#03` [Three](https://example.com/3)\r\n- `#04` [Four](https://example.com/4)\r\n- `#05` [Five](https://example.com/5)\r\n{END}\r\nfooter\r\n'.encode())

    def test_escapes_title_and_encodes_link(self):
        result = self.run_updater(feed([
            ('A [link] *bold* <img> & \\ code`\nnext', 'https://example.com/a (b)/中文?q=x y&z=1')
        ]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('- `#01` [A \\[link\\] \\*bold\\* &lt;img&gt; &amp; \\\\ code\\` next](https://example.com/a%20%28b%29/%E4%B8%AD%E6%96%87?q=x%20y&z=1)', self.readme.read_text())

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

    def test_extracts_subtitle_class_token_and_nested_text(self):
        page = ('<title>lec04</title><h1>lec04</h1>'
                '<span class="title-secondary">Navigation</span>'
                '<p class="note-subtitle astro-123">Skills <em>and</em> Memory &amp; Tools</p>'
                '<p>Unrelated text</p>')
        self.assertEqual(update_notes.extract_subtitle(page), 'Skills and Memory & Tools')
        self.assertEqual(update_notes.extract_subtitle('<p class="not-note-subtitle">No</p>'), '')

    def test_enriched_title_is_escaped_before_rendering(self):
        update_notes.update_readme(
            self.readme, feed([('lec04', 'https://lihaoze-bob.github.io/lec04/')]),
            load_page=lambda url: '<p class="note-subtitle">Skills [x] *and* &lt;Memory&gt;</p>',
        )
        self.assertIn('- `#01` [Skills \\[x\\] \\*and\\* &lt;Memory&gt;]', self.readme.read_text())

    def test_missing_or_empty_subtitle_preserves_rss_title(self):
        for page in ['<h1>lec04</h1><span class="title-secondary">Navigation</span>',
                     '<p class="note-subtitle">  </p>']:
            with self.subTest(page=page):
                update_notes.update_readme(
                    self.readme, feed([('lec04', 'https://lihaoze-bob.github.io/lec04/')]),
                    load_page=lambda url: page,
                )
                self.assertIn('- `#01` [lec04](https://lihaoze-bob.github.io/lec04/)', self.readme.read_text())

    def test_page_failure_warns_and_falls_back_without_erasing_notes(self):
        def failed_loader(url):
            raise URLError('unavailable')
        warnings = io.StringIO()
        with contextlib.redirect_stderr(warnings):
            update_notes.update_readme(
                self.readme, feed([('lec04', 'https://lihaoze-bob.github.io/lec04/')]),
                load_page=failed_loader,
            )
        self.assertIn('Warning', warnings.getvalue())
        self.assertIn('- `#01` [lec04](https://lihaoze-bob.github.io/lec04/)', self.readme.read_text())

    def test_enrichment_fetches_only_selected_same_origin_pages(self):
        loaded = []
        def loader(url):
            loaded.append(url)
            return '<p class="note-subtitle">Full title</p>'
        update_notes.update_readme(self.readme, feed([
            ('External', 'https://other.example/article'),
            ('Wrong scheme', 'http://lihaoze-bob.github.io/article'),
            ('Own', 'https://lihaoze-bob.github.io/lec04/'),
            ('Fourth', 'https://lihaoze-bob.github.io/lec03/'),
            ('Fifth', 'https://lihaoze-bob.github.io/lec02/'),
            ('Extra', 'https://lihaoze-bob.github.io/lec01/'),
        ]), load_page=loader)
        self.assertEqual(loaded, ['https://lihaoze-bob.github.io/lec04/',
                                  'https://lihaoze-bob.github.io/lec03/',
                                  'https://lihaoze-bob.github.io/lec02/'])
        self.assertIn('- `#03` [Full title]', self.readme.read_text())

    def test_live_mode_enriches_but_local_feed_mode_remains_offline(self):
        xml = feed([('lec04', 'https://lihaoze-bob.github.io/lec04/')]).encode()
        loaded = []
        def loader(url):
            loaded.append(url)
            return xml if url == update_notes.FEED_URL else b'<p class="note-subtitle">Skills and Memory</p>'
        with patch.object(update_notes, 'fetch_url', side_effect=loader):
            self.assertEqual(update_notes.main(['--readme', str(self.readme)]), 0)
        self.assertEqual(loaded, [update_notes.FEED_URL, 'https://lihaoze-bob.github.io/lec04/'])
        self.assertIn('- `#01` [Skills and Memory]', self.readme.read_text())
        self.feed.write_bytes(xml)
        def offline_loader(url):
            self.fail('Local feed mode attempted a network request')
        with patch.object(update_notes, 'fetch_url', side_effect=offline_loader):
            self.assertEqual(update_notes.main(['--readme', str(self.readme), '--feed-file', str(self.feed)]), 0)
        self.assertIn('- `#01` [lec04]', self.readme.read_text())

    def test_missing_feed_file_leaves_readme_untouched(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), '--feed-file', str(self.feed),
             '--readme', str(self.readme)], capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.readme.read_bytes(), ORIGINAL.encode())


if __name__ == '__main__':
    unittest.main()
