#!/usr/bin/env python3
"""Refresh the marked latest-notes list from an RSS feed (standard library only)."""
import argparse
import html
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

FEED_URL = 'https://lihaoze-bob.github.io/feed.xml'
START = '<!-- notes:start -->'
END = '<!-- notes:end -->'


def parse_feed(data):
    """Return the first five unique, usable RSS titles and HTTP(S) links."""
    root = ET.fromstring(data)
    if root.tag != 'rss' or root.find('channel') is None:
        raise ValueError('Expected an RSS feed with a channel')
    notes = []
    seen = set()
    for item in root.findall('./channel/item'):
        title = ' '.join((item.findtext('title') or '').split())
        url = (item.findtext('link') or '').strip()
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in ('http', 'https') and bool(parsed.hostname)
            valid = valid and not any(ord(char) < 32 for char in url)
            parsed.port  # Reject malformed ports as unusable links.
        except ValueError:
            valid = False
        if not title or not valid:
            continue
        url = quote(url, safe=':/?#@!$&\'*,;=+-%_~')
        if url in seen:
            continue
        seen.add(url)
        notes.append((title, url))
        if len(notes) == 5:
            break
    if not notes:
        raise ValueError('RSS feed contains no usable notes')
    return notes


class SubtitleParser(HTMLParser):
    """Collect visible inline text from the first nonempty note subtitle."""
    VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.parts = []
        self.subtitle = ''

    def handle_starttag(self, tag, attrs):
        if self.subtitle:
            return
        classes = (dict(attrs).get('class') or '').split()
        if self.tags or 'note-subtitle' in classes:
            if tag not in self.VOID_TAGS:
                self.tags.append(tag)
            elif tag == 'br':
                self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in self.tags:
            self.tags = self.tags[:len(self.tags) - 1 - self.tags[::-1].index(tag)]
            if not self.tags:
                self.subtitle = ' '.join(''.join(self.parts).split())
                self.parts = []

    def handle_data(self, data):
        if self.tags:
            self.parts.append(data)


def extract_subtitle(page):
    parser = SubtitleParser()
    parser.feed(page.decode('utf-8') if isinstance(page, bytes) else page)
    parser.close()
    return parser.subtitle


def same_origin(url):
    source, target = urlsplit(FEED_URL), urlsplit(url)
    return (target.scheme, target.hostname, target.port or (443 if target.scheme == 'https' else 80)) == (
        source.scheme, source.hostname, source.port or (443 if source.scheme == 'https' else 80))


def fetch_url(url):
    request = Request(url, headers={'User-Agent': 'github-profile-notes/1.0'})
    with urlopen(request, timeout=20) as response:
        return response.read()


def update_readme(readme, data, load_page=None):
    """Replace only the marker contents, writing only when bytes change."""
    original = readme.read_bytes()
    text = original.decode('utf-8')
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError('README must contain exactly one notes:start/end marker pair')
    start = text.index(START) + len(START)
    end = text.index(END)
    if start > end:
        raise ValueError('README notes markers are reversed')
    lines = []
    for number, (title, url) in enumerate(parse_feed(data), start=1):
        if load_page is not None and same_origin(url):
            try:
                title = extract_subtitle(load_page(url)) or title
            except (OSError, ValueError, HTTPException) as error:
                print(f'Warning: could not load subtitle from {url}: {error}', file=sys.stderr)
        title = re.sub(r'([\\`*_[\]~])', r'\\\1', html.escape(title, quote=False))
        lines.append(f'- `#{number:02d}` [{title}]({url})')
    newline = '\r\n' if text[start:].startswith('\r\n') else '\n'
    updated = (text[:start] + newline + newline.join(lines) + newline + text[end:]).encode('utf-8')
    if updated == original:
        return False
    readme.write_bytes(updated)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feed-file', type=Path, help='Read a local RSS file instead of fetching')
    parser.add_argument('--readme', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'README.md')
    args = parser.parse_args(argv)
    try:
        if args.feed_file:
            data = args.feed_file.read_bytes()
        else:
            data = fetch_url(FEED_URL)
        changed = update_readme(args.readme, data, load_page=None if args.feed_file else fetch_url)
    except (OSError, ValueError, ET.ParseError, HTTPException) as error:
        print(f'Could not update notes: {error}', file=sys.stderr)
        return 1
    print('Updated latest notes.' if changed else 'Latest notes are unchanged.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
