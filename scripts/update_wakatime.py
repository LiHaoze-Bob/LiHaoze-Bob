#!/usr/bin/env python3
"""Update only the WakaTime README section using seven local calendar days."""
import argparse
from datetime import datetime, timedelta, timezone
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
OBSIDIAN_START = b'<!--START_SECTION:obsidian-->'
OBSIDIAN_END = b'<!--END_SECTION:obsidian-->'


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


def aggregate(payload, category='languages'):
    def seconds(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise UpdateError('Invalid WakaTime duration.')
        return value

    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise UpdateError('Invalid or incomplete WakaTime summary.')
    total = 0
    entries = {}
    for day in payload['data']:
        if not isinstance(day, dict) or not isinstance(day.get('grand_total'), dict) or not isinstance(day.get(category), list):
            raise UpdateError('Invalid WakaTime daily summary.')
        total += seconds(day['grand_total'].get('total_seconds'))
        for entry in day[category]:
            if not isinstance(entry, dict):
                raise UpdateError('Invalid WakaTime entry summary.')
            name = entry.get('name')
            if not isinstance(name, str) or not name.strip() or '`' in name or any(ord(char) < 32 or ord(char) == 127 for char in name):
                raise UpdateError('Invalid WakaTime entry name.')
            entries[name] = entries.get(name, 0) + seconds(entry.get('total_seconds'))
    if not math.isfinite(total) or any(not math.isfinite(value) for value in entries.values()):
        raise UpdateError('Invalid WakaTime duration total.')
    return total, sorted(entries.items(), key=lambda item: (-item[1], item[0]))


def format_time(seconds):
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours} hr{ "s" if hours != 1 else ""} {minutes} mins'
    return f'{minutes} mins' if minutes else f'{int(seconds)} secs'


def render(payload, start, end):
    total, languages = aggregate(payload)
    _, editors = aggregate(payload, 'editors')
    if sum(value for _, value in editors) > total + 1:
        raise UpdateError('Inconsistent WakaTime editor totals.')
    # WakaTime reports the Codex integration as "Codex Vscode".
    codex = sum(value for name, value in editors if name.strip().casefold() in {'codex', 'codex vscode'})
    vscode = sum(value for name, value in editors if name.strip().casefold() == 'vs code')
    lines = ['```txt', 'Last 7 days (including today)',
             f'{start.isoformat()} - {end.isoformat()} ({TIMEZONE})',
             f'Total Time: {format_time(total)}', '',
             'Editors', f'Codex: {format_time(codex)}', f'VS Code: {format_time(vscode)}',
             '', 'Languages']
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


def render_obsidian(payload, updated_at=None):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise UpdateError('Invalid or incomplete WakaTime summary.')
    total = 0
    for day in payload['data']:
        if not isinstance(day, dict) or not isinstance(day.get('editors'), list):
            raise UpdateError('WakaTime editor summaries are missing or invalid.')
        for editor in day['editors']:
            if not isinstance(editor, dict) or not isinstance(editor.get('name'), str):
                raise UpdateError('Invalid WakaTime editor summary.')
            value = editor.get('total_seconds')
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise UpdateError('Invalid WakaTime editor duration.')
            if editor['name'].strip().casefold() == 'obsidian':
                total += value
    if not math.isfinite(total):
        raise UpdateError('Invalid WakaTime editor duration total.')
    if total == 0:
        text = '🔮 Time @Obsidian · No activity tracked in the last 7 days yet.'
    else:
        text = f'🔮 Time @Obsidian · **{format_time(total)}** in the last 7 days'
    if updated_at is not None:
        stamp = updated_at.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        text += f'\n<!-- activity-updated-at: {stamp} -->'
    return text


def replace_section(original, content, start_marker=START, end_marker=END):
    if original.count(start_marker) != 1 or original.count(end_marker) != 1:
        raise UpdateError('README must contain exactly one pair of WakaTime markers.')
    start = original.index(start_marker) + len(start_marker)
    end = original.index(end_marker)
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
        if OBSIDIAN_START in original or OBSIDIAN_END in original:
            updated = replace_section(updated, render_obsidian(data, datetime.now(timezone.utc)), OBSIDIAN_START, OBSIDIAN_END)
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
