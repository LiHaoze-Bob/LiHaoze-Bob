#!/usr/bin/env python3
"""Update only the WakaTime README section using seven local calendar days."""
import argparse
from datetime import datetime, timedelta
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

TIMEZONE = 'Asia/Shanghai'
START = b'<!--START_SECTION:waka-->'
END = b'<!--END_SECTION:waka-->'


class UpdateError(Exception):
    """An expected failure whose message is safe to print."""


def date_range(now=None):
    today = (now or datetime.now(ZoneInfo(TIMEZONE))).astimezone(ZoneInfo(TIMEZONE)).date()
    return today - timedelta(days=6), today


def fetch(start, end, token):
    if not token or not token.strip():
        raise UpdateError('WAKATIME_TOKEN is required.')
    query = urlencode({'start': start.isoformat(), 'end': end.isoformat(), 'timezone': TIMEZONE})
    request = Request('https://api.wakatime.com/api/v1/users/current/summaries?' + query,
                      headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise UpdateError('WakaTime did not return a completed summary (HTTP 200 required).')
            return json.loads(response.read())
    except HTTPError as exc:
        raise UpdateError(f'WakaTime request failed (HTTP {exc.code}).') from None
    except (URLError, HTTPException, OSError, ValueError):
        raise UpdateError('Unable to retrieve a valid WakaTime summary.') from None


def aggregate(payload):
    def seconds(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise UpdateError('Invalid WakaTime duration.')
        return value

    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise UpdateError('Invalid or incomplete WakaTime summary.')
    total = 0
    languages = {}
    for day in payload['data']:
        if not isinstance(day, dict) or not isinstance(day.get('grand_total'), dict) or not isinstance(day.get('languages'), list):
            raise UpdateError('Invalid WakaTime daily summary.')
        total += seconds(day['grand_total'].get('total_seconds'))
        for language in day['languages']:
            if not isinstance(language, dict):
                raise UpdateError('Invalid WakaTime language summary.')
            name = language.get('name')
            if not isinstance(name, str) or not name.strip() or '`' in name or any(ord(char) < 32 or ord(char) == 127 for char in name):
                raise UpdateError('Invalid WakaTime language name.')
            languages[name] = languages.get(name, 0) + seconds(language.get('total_seconds'))
    if not math.isfinite(total) or any(not math.isfinite(value) for value in languages.values()):
        raise UpdateError('Invalid WakaTime duration total.')
    return total, sorted(languages.items(), key=lambda item: (-item[1], item[0]))


def format_time(seconds):
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours} hr{ "s" if hours != 1 else ""} {minutes} mins'
    return f'{minutes} mins' if minutes else f'{int(seconds)} secs'


def render(payload, start, end):
    total, languages = aggregate(payload)
    lines = ['```txt', 'Last 7 days (including today)',
             f'{start.isoformat()} - {end.isoformat()} ({TIMEZONE})',
             f'Total Time: {format_time(total)}', '']
    if total == 0:
        if any(value > 0 for _, value in languages):
            raise UpdateError('Inconsistent WakaTime totals.')
        lines.append('No activity tracked in this period.')
    else:
        if not languages or sum(value for _, value in languages) > total + 1:
            raise UpdateError('Inconsistent WakaTime totals.')
        rows = [(name, value) for name, value in languages if value > 0][:5]
        width = max((len(name) for name, _ in rows), default=0)
        for name, value in rows:
            percent = value / total * 100
            filled = round(percent / 100 * 20)
            bar = '█' * filled + '░' * (20 - filled)
            lines.append(f'{name:<{width}}  {format_time(value):>14}  {bar}  {percent:6.2f}%')
    return '\n'.join(lines + ['```'])


def replace_section(original, content):
    if original.count(START) != 1 or original.count(END) != 1:
        raise UpdateError('README must contain exactly one pair of WakaTime markers.')
    start = original.index(START) + len(START)
    end = original.index(END)
    if start > end:
        raise UpdateError('README WakaTime markers are reversed.')
    newline = b'\r\n' if b'\r\n' in original else b'\n'
    return original[:start] + newline + content.encode('utf-8').replace(b'\n', newline) + newline + original[end:]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--readme', type=Path, default=Path(__file__).resolve().parents[1] / 'README.md')
    parser.add_argument('--data-file', type=Path, help='Read an offline summaries JSON fixture instead of the API')
    args = parser.parse_args(argv)
    try:
        original = args.readme.read_bytes()
        start, end = date_range()
        data = json.loads(args.data_file.read_bytes()) if args.data_file else fetch(start, end, os.environ.get('WAKATIME_TOKEN', ''))
        updated = replace_section(original, render(data, start, end))
        if updated != original:
            args.readme.write_bytes(updated)
    except UpdateError as exc:
        print(f'WakaTime update failed: {exc}', file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print('WakaTime update failed: unable to read or write updater files.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
