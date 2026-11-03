#!/usr/bin/env python3
"""Refresh the marked latest-notes list from an RSS feed (standard library only)."""
import argparse
import html
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
    """Return the first three unique, usable RSS titles and HTTP(S) links."""
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
        if len(notes) == 3:
            break
    if not notes:
        raise ValueError('RSS feed contains no usable notes')
    return notes


def update_readme(readme, data):
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
    for title, url in parse_feed(data):
        title = re.sub(r'([\\`*_[\]~])', r'\\\1', html.escape(title, quote=False))
        lines.append(f'- [{title}]({url})')
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
            request = Request(FEED_URL, headers={'User-Agent': 'github-profile-notes/1.0'})
            with urlopen(request, timeout=20) as response:
                data = response.read()
        changed = update_readme(args.readme, data)
    except (OSError, ValueError, ET.ParseError) as error:
        print(f'Could not update notes: {error}', file=sys.stderr)
        return 1
    print('Updated latest notes.' if changed else 'Latest notes are unchanged.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
