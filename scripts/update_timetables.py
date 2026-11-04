#!/usr/bin/env python3
"""Read each venue's official opening-times page and write data/timetables.json,
which the site loads.

    python3 scripts/update_timetables.py              update every venue
    python3 scripts/update_timetables.py --only=heath,brockwell
    python3 scripts/update_timetables.py --dry-run    print the result, write nothing
    python3 scripts/update_timetables.py --out=path/to/file.json

Needs Python 3.9+ and nothing else (standard library only).

If a page can't be fetched or no longer looks the way its parser expects, that
venue keeps the times from the previous run, marked "stale", and the script
exits with code 1 so a cron job can report it. One broken page never wipes the
others.

Output shape (per venue):
  { name, lat, lon, keywords, bookingUrl, source: { name, url },
    status: "ok" | "stale", checkedAt, error?, note?, publishedUntil?,
    periods: [ { from, to, days?, replace?, sessions: [
      { name, open, lastEntry?, close, booking } ] } ] }
Dates are "YYYY-MM-DD", times "HH:MM" (London), days are 0 = Sunday … 6.
A session's `close` is when swimmers must leave the water. `booking` is
"required", "advised" or null. On any date every matching period's sessions
apply, unless a matching period has `replace: true` (e.g. bank holidays).
"""

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / 'data' / 'timetables.json'
HORIZON_DAYS = 400
BETTER_WEEKS = 4      # Better pages publish one week at a time
STANDING_WEEKS = 8    # how far ahead to repeat an undated weekly timetable
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36'

# --- venues -------------------------------------------------------------------

HEATH_TIMES = 'https://www.cityoflondon.gov.uk/things-to-do/green-spaces/hampstead-heath/activities-at-hampstead-heath/swimming-at-hampstead-heath/swimming-opening-times'
HEATH_SOURCE = {'name': 'City of London', 'url': HEATH_TIMES}
HEATH_BOOKING = 'https://www.cityoflondon.gov.uk/things-to-do/green-spaces/hampstead-heath/activities-at-hampstead-heath/swimming-at-hampstead-heath/booking-for-swimming'
MIXED_POND_PAGE = 'https://www.cityoflondon.gov.uk/things-to-do/green-spaces/hampstead-heath/where-to-go-at-hampstead-heath/mixed-pond'

# Everything the site shows about a venue that isn't its timetable.
VENUES = {
    'mixed': {
        'name': 'Hampstead Mixed Pond', 'lat': 51.5591, 'lon': -0.1645,
        'keywords': ['hampstead heath', 'mixed bathing'], 'bookingUrl': HEATH_BOOKING, 'source': HEATH_SOURCE,
    },
    'ladies': {
        'name': 'Kenwood Ladies’ Pond', 'lat': 51.5667, 'lon': -0.1573,
        'keywords': ['hampstead heath', 'womens', 'women', 'kenwood'], 'bookingUrl': HEATH_BOOKING, 'source': HEATH_SOURCE,
    },
    'mens': {
        'name': 'Highgate Men’s Pond', 'lat': 51.5629, 'lon': -0.1556,
        'keywords': ['hampstead heath', 'highgate'], 'bookingUrl': HEATH_BOOKING, 'source': HEATH_SOURCE,
    },
    'lido': {
        'name': 'Parliament Hill Lido', 'lat': 51.5557, 'lon': -0.1497,
        'keywords': ['hampstead heath', 'gospel oak', 'pool'], 'bookingUrl': HEATH_BOOKING, 'source': HEATH_SOURCE,
    },
    'brockwell': {
        'name': 'Brockwell Lido', 'lat': 51.4544, 'lon': -0.1090,
        'keywords': ['brixton', 'herne hill', 'brockwell park', 'lambeth', 'pool'],
        'bookingUrl': 'https://lambethcouncil.bookings.flow.onl/location/brockwell-lido/lido-lane-swim/',
        'source': {'name': 'Active Lambeth', 'url': 'https://active.lambeth.gov.uk/locations/brockwell-lido/'},
    },
    'londonfields': {
        'name': 'London Fields Lido', 'lat': 51.5431, 'lon': -0.0625,
        'keywords': ['hackney', 'pool'],
        'bookingUrl': 'https://www.better.org.uk/leisure-centre/london/hackney/london-fields-lido/activities',
        'source': {'name': 'Better', 'url': 'https://www.better.org.uk/leisure-centre/london/hackney/london-fields-lido'},
    },
    'oasis': {
        'name': 'Oasis Outdoor Pool', 'lat': 51.5157, 'lon': -0.1259,
        'keywords': ['covent garden', 'holborn', 'oasis sports centre', 'camden', 'lido'],
        'bookingUrl': 'https://www.better.org.uk/leisure-centre/london/camden/oasis-sports-centre/activities',
        'source': {'name': 'Better', 'url': 'https://www.better.org.uk/leisure-centre/london/camden/oasis-sports-centre'},
    },
    'parkroad': {
        'name': 'Park Road Lido', 'lat': 51.5823, 'lon': -0.1280,
        'keywords': ['hornsey', 'crouch end', 'haringey', 'park road pools', 'pool'],
        'bookingUrl': 'https://haringeyactivewellbeing.bookings.flow.onl/',
        'source': {'name': 'Haringey Council', 'url': 'https://haringey.gov.uk/leisure-parks-culture/leisure-centres/park-road-leisure-centre-lido/facilities-park-road/lido-park-road'},
    },
    'tooting': {
        'name': 'Tooting Bec Lido', 'lat': 51.4325, 'lon': -0.1397,
        'keywords': ['tooting', 'streatham', 'wandsworth', 'pool'],
        'bookingUrl': 'https://www.placesleisure.org/centres/tooting-bec-lido/',
        'source': {'name': 'South London Swimming Club', 'url': 'https://slsc.org.uk/opening-times/'},
    },
    'serpentine': {
        'name': 'Serpentine Lido', 'lat': 51.5080, 'lon': -0.1663,
        'keywords': ['hyde park', 'the serpentine', 'royal parks', 'lake'],
        'bookingUrl': 'https://sportsandleisureroyalparks.bookings.flow.onl/',
        'source': {'name': 'The Royal Parks', 'url': 'https://sportsandleisure.royalparks.org.uk/hyde-park/swimming'},
    },
    'charlton': {
        'name': 'Charlton Lido', 'lat': 51.4740, 'lon': 0.0382,
        'keywords': ['charlton', 'greenwich', 'woolwich', 'hornfair park', 'pool'],
        'bookingUrl': 'https://www.better.org.uk/leisure-centre/london/greenwich/charlton-lido/activities',
        'source': {'name': 'Better', 'url': 'https://www.better.org.uk/leisure-centre/london/greenwich/charlton-lido'},
    },
    'hampton': {
        'name': 'Hampton Pool', 'lat': 51.4176, 'lon': -0.3584,
        'keywords': ['hampton', 'richmond', 'twickenham', 'bushy park', 'lido'],
        'bookingUrl': 'https://southdownsleisure.legendonlineservices.co.uk/hampton_pools/bookingscentre/membertimetable',
        'source': {'name': 'Hampton Pool', 'url': 'https://hamptonpool.co.uk/'},
    },
    'finchley': {
        'name': 'Finchley Lido', 'lat': 51.6048, 'lon': -0.1734,
        'keywords': ['finchley', 'north finchley', 'barnet', 'pool'],
        'bookingUrl': 'https://www.better.org.uk/leisure-centre/london/barnet/finchley-lido-leisure-centre/activities',
        'source': {'name': 'Better', 'url': 'https://www.better.org.uk/leisure-centre/london/barnet/finchley-lido-leisure-centre/lido'},
    },
    'beckenham': {
        'name': 'Beckenham Place Park Lake', 'lat': 51.4203, 'lon': -0.0173,
        'keywords': ['beckenham', 'lewisham', 'catford', 'bromley', 'lake', 'wild swimming', 'open water'],
        'bookingUrl': 'https://booking.sportsguardian.com/ptp-at-beckenham-place-park',
        'source': {'name': 'PTP Coaching', 'url': 'https://www.ptp.org.uk/'},
    },
}

# --- dates & times ------------------------------------------------------------

MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
               'August', 'September', 'October', 'November', 'December']
DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']   # index = day number, 0 = Sunday


def london_today():
    return datetime.now(ZoneInfo('Europe/London')).date().isoformat()


def iso_of(y, m, d):
    """m is 1-12."""
    return f'{y:04d}-{m:02d}-{d:02d}'


def add_days(iso, n):
    return (date.fromisoformat(iso) + timedelta(days=n)).isoformat()


def day_of(iso):
    """0 = Sunday … 6 = Saturday."""
    return (date.fromisoformat(iso).weekday() + 1) % 7


def monday_of(iso):
    return add_days(iso, -((day_of(iso) + 6) % 7))


def month_index(word):
    """1-12, or 0 if `word` isn't a month name."""
    key = (word or '').lower()[:3]
    return MONTHS.index(key) + 1 if key in MONTHS else 0


def nearest_year(month, day, ref):
    """The year that puts month/day closest to `ref` (for dates printed without a year)."""
    y, ref_date = int(ref[:4]), date.fromisoformat(ref)
    candidates = []
    for yy in (y - 1, y, y + 1):
        try:
            candidates.append((abs((date(yy, month, day) - ref_date).days), yy))
        except ValueError:
            pass
    return min(candidates)[1]


DATE_RANGE = re.compile(
    r'(\d{1,2})(?:st|nd|rd|th)?(?:\s+([A-Za-z]{3,}))?(?:\s+(\d{4}))?\s*(?:to|–|—|-)\s*'
    r'(?:[A-Za-z]+day\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,})(?:\s+(\d{4}))?')


def parse_date_range(text, ref):
    """"15 February to 28 March 2026", "16 to 29 August 2026", "1 May – 31 August",
    "22 November 2026 to 13 February 2027", "28 September 2026 to Friday 30 April 2027".
    Missing years are filled from the end date, or the nearest year to `ref`."""
    m = DATE_RANGE.search(text)
    if not m:
        return None
    d1, mon1, y1, d2, mon2, y2 = m.groups()
    m2 = month_index(mon2)
    m1 = month_index(mon1) if mon1 else m2
    if not m1 or not m2:
        return None
    if y2:
        end_year = int(y2)
    elif y1:
        end_year = int(y1) + (1 if m1 > m2 else 0)
    else:
        end_year = nearest_year(m2, int(d2), ref)
    if y1:
        start_year = int(y1)
    else:
        start_year = end_year - 1 if (m1 > m2 or (m1 == m2 and int(d1) > int(d2))) else end_year
    return {'from': iso_of(start_year, m1, int(d1)), 'to': iso_of(end_year, m2, int(d2)), 'text': m.group(0)}


def parse_date(text, ref):
    """"Sunday 25 October 2026" or "25 October 2026" (or without a year, near `ref`)."""
    m = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,})(?:\s+(\d{4}))?', text or '')
    if not m or not month_index(m.group(2)):
        return None
    month, day = month_index(m.group(2)), int(m.group(1))
    return iso_of(int(m.group(3)) if m.group(3) else nearest_year(month, day, ref), month, day)


def parse_time(raw):
    """"7am", "2.15pm", "12pm", "06:30", "5.pm" -> "HH:MM"."""
    s = re.sub(r'\.(?=\s*[ap]m)', '', raw.strip().lower(), count=1)
    s = re.sub(r'\s+', '', s)
    if s in ('noon', 'midday'):
        return '12:00'
    m = re.fullmatch(r'(\d{1,2})(?:[.:](\d{2}))?(am|pm)?', s)
    if not m:
        raise ValueError(f'unrecognised time "{raw}"')
    h, minute = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == 'pm' and h < 12:
        h += 12
    if m.group(3) == 'am' and h == 12:
        h = 0
    if h > 23 or minute > 59:
        raise ValueError(f'impossible time "{raw}"')
    return f'{h:02d}:{minute:02d}'


TIME_RANGE = re.compile(
    r'(\d{1,2}(?:[.:]\d{2})?\s*(?:am|pm)?)\s*(?:–|—|-|to)\s*(\d{1,2}(?:[.:]\d{2})?\.?\s*(?:am|pm)?)', re.I)


def time_ranges(text):
    """Every "7am – 2.30pm" / "06:30 - 21:00" range in a string, as (open, close)."""
    t = re.sub(r'\s+or\s+sun(?:rise|set)\*?', '', text, flags=re.I)
    looks_like_time = lambda s: re.search(r'am|pm|:', s, re.I)
    return [(parse_time(a), parse_time(b)) for a, b in TIME_RANGE.findall(t)
            if looks_like_time(a) or looks_like_time(b)]


def parse_day_range(text):
    """"Mon - Fri", "Sat - Sun", "Mon - Sun", "Tue" -> [day numbers]."""
    m = re.fullmatch(r'(mon|tue|wed|thu|fri|sat|sun)[a-z]*(?:\s*(?:-|–|to)\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*)?',
                     text.strip().lower())
    if not m:
        return None
    a = DAYS.index(m.group(1))
    b = DAYS.index(m.group(2)) if m.group(2) else a
    out, d = [a], a
    while d != b:
        d = (d + 1) % 7
        out.append(d)
    return sorted(out)


# --- html ---------------------------------------------------------------------

def squash(s):
    return re.sub(r'\s+', ' ', s.replace('‑', '-')).strip()


def cell_text(fragment):
    return squash(html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'<br\s*/?>', ' ', fragment, flags=re.I))))


def text_lines(src):
    """The page as lines of visible text, roughly one per block element."""
    s = re.sub(r'<(script|style|noscript|svg|template)\b.*?</\1>', ' ', src, flags=re.I | re.S)
    s = re.sub(r'<br\s*/?>|</(?:p|div|li|tr|h\d|td|th|table|section|ul|ol|dt|dd)>', '\n', s, flags=re.I)
    s = html.unescape(re.sub(r'<[^>]+>', ' ', s))
    return [line for line in (squash(x) for x in s.split('\n')) if line]


def tables(src):
    """Every <table> as rows of cell text. `before` is the text just above it plus
    its <caption> (where these pages put headings like "Lido opening times
    from …"); `after` is the text just below it (footnotes)."""
    out = []
    for m in re.finditer(r'<table\b.*?</table>', src, flags=re.I | re.S):
        table = m.group(0)
        caption = re.search(r'<caption\b[^>]*>(.*?)</caption>', table, flags=re.I | re.S)
        above = ' '.join(text_lines(src[max(0, m.start() - 4000):m.start()])[-3:])
        rows = []
        # Split on each <tr> rather than matching <tr>…</tr>: some pages leave rows unclosed.
        for row in re.split(r'<tr\b', table, flags=re.I)[1:]:
            cells = [cell_text(c.group(1)) for c in re.finditer(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row, flags=re.I | re.S)]
            if cells:
                rows.append(cells)
        out.append({
            'rows': rows,
            'before': f'{above} {cell_text(caption.group(1))}' if caption else above,
            'after': ' '.join(text_lines(src[m.end():m.end() + 2000])[:2]),
        })
    return out


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.read().decode(res.headers.get_content_charset() or 'utf-8', errors='replace')
    except urllib.error.HTTPError as err:
        raise RuntimeError(f'{url} answered HTTP {err.code}') from None
    except urllib.error.URLError as err:
        raise RuntimeError(f'{url} unreachable ({err.reason})') from None


def href_matching(src, pattern):
    m = re.search(pattern, src)
    return m.group(0) if m else None


def overlap(a, b):
    start = max(a['from'], b['from'])
    end = min(a['to'], b['to'])
    return {'from': start, 'to': end} if start <= end else None


def format_day_month(iso):
    d = date.fromisoformat(iso)
    return f'{d.day} {MONTH_NAMES[d.month - 1]}'


# --- sources ------------------------------------------------------------------

def heath_source(today):
    """City of London: the bathing ponds' seasonal table, the summer session table,
    the lido's summer and winter tables, and the Mixed Pond's own season notice."""
    all_tables = tables(get(HEATH_TIMES))

    def find_table(test, what):
        for t in all_tables:
            if t['rows'] and test(t):
                return t
        raise ValueError(f"couldn't find the {what} table")

    first_cell = lambda t: t['rows'][0][0]
    pond_table = find_table(lambda t: re.search(r'date from', first_cell(t), re.I), 'bathing ponds opening times')
    pond_sessions = find_table(lambda t: re.fullmatch(r'session', first_cell(t), re.I) and re.search(r'bathing pond', t['before'], re.I), 'bathing pond sessions')
    lido_summer = find_table(lambda t: re.fullmatch(r'session', first_cell(t), re.I) and re.search(r'lido opening times from', t['before'], re.I), 'lido summer')
    lido_winter = find_table(lambda t: re.fullmatch(r'open', first_cell(t), re.I) and re.search(r'lido opening times from', t['before'], re.I), 'lido winter')

    def session_from_row(name, row):
        """row: [open, last entry, vacate water, vacate facility]"""
        return {
            'name': name,
            'open': parse_time(row[0]),
            'lastEntry': parse_time(row[1]),
            'close': parse_time(row[2]),
            'booking': 'required' if re.search(r'bookable', name, re.I) else None,
        }

    def clean_name(raw):
        return re.sub(r'\([^)]*\)', '', raw.replace('*', '')).strip()

    def qualifier(raw, within):
        """"(until 29 Aug)", "(from 30 Aug)", "(from 16 to 29 Aug)" -> date limits inside `within`."""
        q = re.search(r'\((until|from)\s+([^)]*)\)', raw, re.I)
        if not q:
            return {'from': within['from'], 'to': within['to']}
        ref = within['from']
        if q.group(1).lower() == 'until':
            return {'from': within['from'], 'to': parse_date(q.group(2), ref)}
        rng = parse_date_range(q.group(2), ref)
        return {'from': rng['from'], 'to': rng['to']} if rng else {'from': parse_date(q.group(2), ref), 'to': within['to']}

    # Ponds: plain rows are one drop-in session; starred rows use the summer session table.
    pond_periods = []
    for row in pond_table['rows'][1:]:
        rng = parse_date_range(row[0], today)
        if not rng:
            raise ValueError(f'unreadable pond date range "{row[0]}"')
        if '*' not in row[0]:
            pond_periods.append({'from': rng['from'], 'to': rng['to'], 'sessions': [session_from_row('Drop-in swimming', row[1:])]})
            continue
        for s in pond_sessions['rows'][1:]:
            span = overlap(rng, qualifier(s[0], rng))
            if span:
                pond_periods.append({**span, 'sessions': [session_from_row(clean_name(s[0]), s[1:])]})
    if len(pond_periods) < 3:
        raise ValueError('too few pond periods found')

    # Lido: summer sessions (some weekday-limited), then the winter session.
    summer = parse_date_range(re.search(r'lido opening times from (.*)', lido_summer['before'], re.I).group(1), today)
    not_on = (re.search(r'do not take place on ([^.]*)', lido_summer['after'], re.I) or [None, ''])[1]
    skip_days = [i for i, d in enumerate(DAYS) if re.search(rf'\b{d}', not_on, re.I)]
    # Two rows for the same session that each say "(until …)" follow on from
    # each other: "until 4 September", then "until 25 September" from the 5th.
    until_ends = {}
    lido_periods = []
    for row in lido_summer['rows'][1:]:
        session = session_from_row(clean_name(row[0]), row[1:])
        span = qualifier(row[0], summer)
        key = f"{session['name']} {session['open']}"
        if re.search(r'\(until\b', row[0], re.I):
            if key in until_ends:
                span['from'] = add_days(until_ends[key], 1)
            until_ends[key] = span['to']
        period = {**span, 'sessions': [session]}
        if row[0].strip().startswith('*') and skip_days:
            period['days'] = [d for d in range(7) if d not in skip_days]
        lido_periods.append(period)
    winter = parse_date_range(re.search(r'lido opening times from (.*)', lido_winter['before'], re.I).group(1), today)
    lido_periods.append({'from': winter['from'], 'to': winter['to'], 'sessions': [session_from_row('Winter swimming', lido_winter['rows'][1])]})

    # Mixed Pond: the pond timetable, but only inside its own season.
    mixed_text = ' '.join(text_lines(get(MIXED_POND_PAGE)))
    last_m = re.search(r'final public swimming session[^.]*?(\d{1,2}\s+[A-Za-z]+\s+\d{4})', mixed_text, re.I)
    reopen_m = re.search(r're-?open[^.]*?(\d{1,2}\s+[A-Za-z]+\s+\d{4})', mixed_text, re.I)
    spring = re.search(r're-?open[^.]*?spring\s+(\d{4})', mixed_text, re.I)
    last_day = parse_date(last_m.group(1), today) if last_m else None
    reopen_day = parse_date(reopen_m.group(1), today) if reopen_m else None
    mixed = {}
    if last_day or reopen_day:
        windows = []
        if last_day:
            windows.append({'from': '0000-01-01', 'to': last_day})
        if reopen_day:
            windows.append({'from': reopen_day, 'to': '9999-12-31'})
        mixed['periods'] = [{**p, **span} for p in pond_periods for span in (overlap(p, w) for w in windows) if span]
        if not reopen_day:
            mixed['note'] = f'Closed for the winter. It re-opens in spring {spring.group(1)}.' if spring else 'Closed for the winter.'
    elif re.search(r'from April to October', mixed_text, re.I):
        mixed['periods'] = [part for p in pond_periods for part in months_window(p, 4, 10)]
        mixed['note'] = 'Open for public swimming from April to October.'
    else:
        raise ValueError('couldn’t work out the Mixed Pond season')

    return {
        'mixed': mixed,
        'ladies': {'periods': pond_periods},
        'mens': {'periods': pond_periods},
        'lido': {'periods': lido_periods},
    }


def months_window(period, first, last):
    """The parts of a period that fall in months first..last (1-12), every year."""
    out = []
    for y in range(int(period['from'][:4]), int(period['to'][:4]) + 1):
        start = iso_of(y, first, 1)
        end = add_days(iso_of(y + 1, 1, 1) if last == 12 else iso_of(y, last + 1, 1), -1)
        span = overlap(period, {'from': start, 'to': end})
        if span:
            out.append({**period, **span})
    return out


def brockwell_source(today):
    """Active Lambeth: a table of weeks, each with Mon–Fri and Sat–Sun swim times."""
    src = get(VENUES['brockwell']['source']['url'])
    lines = text_lines(src)
    start = next((i for i, l in enumerate(lines) if re.search(r'pool swim times', l, re.I)), -1)
    if start < 0:
        raise ValueError('no "Pool swim times" section')
    week_re = re.compile(r'\d{1,2}(?: [A-Za-z]+)? to \d{1,2} [A-Za-z]+')
    session = lambda times: [{'name': 'Lido swim', 'open': times[0], 'close': times[1], 'booking': 'advised'}]
    periods = []
    i = start
    while i < len(lines) and not re.search(r'centre opening hours', lines[i], re.I):
        week = week_re.search(lines[i])
        if not week:
            i += 1
            continue
        rng = parse_date_range(week.group(0), today)
        # The next two lines with times are Mon–Fri then Sat–Sun ("This week" badges etc. are skipped).
        found = []
        j = i + 1
        while j < len(lines) and len(found) < 2 and not week_re.search(lines[j]):
            times = time_ranges(lines[j])
            if times:
                found.append(times)
            j += 1
        if not rng or len(found) != 2 or any(len(t) != 1 for t in found):
            raise ValueError(f'unreadable week "{lines[i]}"')
        periods.append({'from': rng['from'], 'to': rng['to'], 'days': [1, 2, 3, 4, 5], 'sessions': session(found[0][0])})
        periods.append({'from': rng['from'], 'to': rng['to'], 'days': [0, 6], 'sessions': session(found[1][0])})
        i = j
    if not periods:
        raise ValueError('no weeks found in the swim times table')
    result = {'periods': periods, 'note': 'Pool times are set week by week.'}
    booking = href_matching(src, r'https://[^"\']*bookings\.flow\.onl/location/brockwell-lido[^"\']*')
    if booking:
        result['bookingUrl'] = booking
    return {'brockwell': result}


def better_source(venue_id, block, session_name, today):
    """Better: "Opening times for week commencing …", one week per page (?start_date=)."""
    base = VENUES[venue_id]['source']['url']
    periods = []
    for k in range(BETTER_WEEKS):
        week = add_days(monday_of(today), 7 * k)
        lines = text_lines(get(f'{base}?start_date={week}'))
        wi = next((i for i, l in enumerate(lines) if re.match(r'opening times for week commencing', l, re.I)), -1)
        if wi < 0:
            raise ValueError('no "Opening times for week commencing" heading')
        shown = re.search(r'([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})', lines[wi])
        shown_iso = shown and iso_of(int(shown.group(3)), month_index(shown.group(1)), int(shown.group(2)))
        if shown_iso != week:
            if k == 0:
                raise ValueError(f'asked for the week of {week} but the page shows "{lines[wi]}"')
            break  # the site doesn't publish that far ahead yet
        bi = next((i for i, l in enumerate(lines) if i > wi and l.lower() == block.lower()), -1)
        if bi < 0:
            raise ValueError(f'no "{block}" times for the week of {week}')
        # Day ranges ("Mon - Thu"), each followed by one or more lines of times or "Closed".
        found = 0
        i = bi + 1
        while i < len(lines):
            days = parse_day_range(lines[i])
            if not days:
                break
            found += 1
            sessions = []
            i += 1
            while i < len(lines) and not parse_day_range(lines[i]):
                times = time_ranges(lines[i])
                if not times and not re.fullmatch(r'closed', lines[i], re.I):
                    break
                sessions += [{'name': session_name, 'open': o, 'close': c, 'booking': 'advised'} for o, c in times]
                i += 1
            if sessions:
                periods.append({'from': week, 'to': add_days(week, 6), 'days': days, 'sessions': sessions})
        if not found:
            raise ValueError(f'no day ranges under "{block}" for the week of {week}')
    return {venue_id: {'periods': periods, 'note': 'Times are published a week at a time.'}}


def park_road_source(today):
    """Haringey: standing lido hours for weekdays, weekends and bank holidays."""
    lines = text_lines(get(VENUES['parkroad']['source']['url']))

    def lido_times(label):
        line = next((l for l in lines if re.search(rf'^{label}\b.*\blido\b', l, re.I)), None)
        times = time_ranges(line[re.search(r'\blido\b', line, re.I).start():]) if line else []
        if len(times) != 1:
            raise ValueError(f'no lido times on the "{label}" line')
        return [{'name': 'Lido swim', 'open': times[0][0], 'close': times[0][1], 'booking': 'advised'}]

    until = add_days(today, STANDING_WEEKS * 7)
    periods = [
        {'from': today, 'to': until, 'days': [1, 2, 3, 4, 5], 'sessions': lido_times('Monday to Friday')},
        {'from': today, 'to': until, 'days': [0, 6], 'sessions': lido_times('Weekends')},
    ]
    bank_holiday = lido_times('Bank holidays')
    for day in bank_holidays(today, until):
        periods.append({'from': day, 'to': day, 'replace': True, 'sessions': bank_holiday})
    return {'parkroad': {'periods': periods, 'note': 'Weather permitting - the lido can close at short notice.'}}


def bank_holidays(start, end):
    """England & Wales bank holidays from GOV.UK; an empty list if it can't be reached."""
    try:
        events = json.loads(get('https://www.gov.uk/bank-holidays.json'))['england-and-wales']['events']
        return [e['date'] for e in events if start <= e['date'] <= end]
    except Exception as err:  # noqa: BLE001 - optional extra, never fatal
        print(f'  ! bank holidays unavailable ({err}); using normal hours', file=sys.stderr)
        return []


def tooting_source(today):
    """South London Swimming Club: the public season and its hours, as sentences."""
    lines = text_lines(get(VENUES['tooting']['source']['url']))
    season_line = next((l for l in lines if re.search(r'open to the public from', l, re.I)), None)
    season = season_line and parse_date_range(re.sub(r'^.*open to the public from', '', season_line, flags=re.I), today)
    if not season:
        raise ValueError('no "open to the public from …" sentence')
    start = max(0, next((i for i, l in enumerate(lines) if re.search(r'summer opening times', l, re.I)), 0))
    end = next((i for i, l in enumerate(lines) if i > start and re.search(r'winter opening times', l, re.I)), len(lines))
    periods = []
    for line in lines[start:end]:
        rng = parse_date_range(line, season['from']) if re.match(r'\d', line) else None
        if not rng or line == season_line:
            continue
        # Only the first sentence with times: later ones are cleaning breaks or members-only hours.
        rest = line[line.index(rng['text']) + len(rng['text']):]
        sentence = next((part for part in re.split(r'\.\s+', rest) if time_ranges(part)), '')
        times = time_ranges(sentence)
        if not times:
            continue
        sessions = [{'name': 'Public swim', 'open': o, 'close': c, 'booking': 'advised'} for o, c in times]
        # "Pool cleared at 7.45pm" is when swimmers leave the water at the end of the day.
        cleared = re.search(r'pool cleared at (\d{1,2}(?:[.:]\d{2})?\s*[ap]m)', rest, re.I)
        if cleared:
            at = parse_time(cleared.group(1))
            if sessions[-1]['open'] < at < sessions[-1]['close']:
                sessions[-1]['close'] = at
        periods.append({'from': max(rng['from'], season['from']), 'to': min(rng['to'], season['to']), 'sessions': sessions})
    if not periods:
        raise ValueError('no public hours found under "Summer opening times"')
    span = f"{format_day_month(season['from'])} to {format_day_month(season['to'])}"
    # The first sentence doubles as the short status in lists.
    if today > season['to']:
        note = (f'Closed to the public for the winter. This year’s public season ran {span}; next year’s dates '
                'aren’t out yet. In winter only South London Swimming Club members can swim.')
    else:
        note = f'Open to the public {span}. In winter only South London Swimming Club members can swim.'
    return {'tooting': {'periods': periods, 'note': note}}


def serpentine_source(today):
    """The Royal Parks: only the closed-for-winter wording is understood so far."""
    lines = text_lines(get(VENUES['serpentine']['source']['url']))
    i = next((i for i, l in enumerate(lines) if re.fullmatch(r'opening times', l, re.I)), -1)
    if i < 0 or i + 1 >= len(lines):
        raise ValueError('no "Opening times" heading')
    seasonality = next((l for l in lines if re.search(r'available on weekends from', l, re.I)), '')
    if re.search(r'closed for winter', lines[i + 1], re.I):
        return {'serpentine': {'periods': [], 'note': f'Closed for winter. {seasonality}'.strip()}}
    raise ValueError(f'opening times now read "{lines[i + 1]}" - the Serpentine parser needs teaching this format')


def merge_spans(spans):
    """Join back-to-back or overlapping (open, close) pairs: lane swim 6-8 + rec swim 8-9:30 -> 6-9:30."""
    merged = []
    for o, c in sorted(spans):
        if merged and o <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], c))
        else:
            merged.append((o, c))
    return merged


def hampton_source(today):
    """Hampton Pool: a weekly grid of lane-swim and recreational-swim times for the
    main pool. The learner pool (booking essential, for lessons and families) is left out."""
    grid = next((t for t in tables(get(VENUES['hampton']['source']['url']))
                 if any(re.search(r'lane swim', c, re.I) for r in t['rows'][:2] for c in r)), None)
    if not grid:
        raise ValueError("couldn't find the swimming timetable")
    header = next(r for r in grid['rows'] if any(re.search(r'lane swim', c, re.I) for c in r))
    columns = [i for i, c in enumerate(header) if re.search(r'lane swim|recreational swim', c, re.I)]
    if len(columns) != 2:
        raise ValueError(f'expected lane and recreational swim columns, found {header}')
    by_day = {}
    for row in grid['rows']:
        days = parse_day_range(row[0].split()[0]) if row and row[0].split() else None
        if not days or len(days) != 1:
            continue  # header and "Aqua Aerobics" rows
        by_day[days[0]] = merge_spans([t for col in columns if col < len(row) for t in time_ranges(row[col])])
    if len(by_day) != 7:
        raise ValueError(f'expected 7 days in the timetable, found {len(by_day)}')
    until = add_days(today, STANDING_WEEKS * 7)
    periods = [{'from': today, 'to': until, 'days': [day],
                'sessions': [{'name': 'Swimming', 'open': o, 'close': c, 'booking': 'advised'} for o, c in spans]}
               for day, spans in sorted(by_day.items()) if spans]
    return {'hampton': {'periods': periods, 'note': 'The timetable can change at short notice - check the Hampton Pool website for one-off closures.'}}


def finchley_source(today):
    """Better's Finchley Lido page: the summer season as dated sentences, e.g.
    "Saturday 23rd May - 10:00 - 16:00", "Tuesday 26th May - Friday 29th May: 11:00 - 18:30",
    "open daily from Monday 20th July - Sunday 13th September 2026" then "Weekdays 10:00 - 18:30"."""
    lines = text_lines(get(VENUES['finchley']['source']['url']))
    season_line = next((l for l in lines if re.search(r're-?open for (?:another|the) season|open for the season', l, re.I)), None)
    season_start = parse_date(season_line, today) if season_line else None
    if not season_start:
        raise ValueError('no "re-open for another season on …" sentence')
    start = lines.index(season_line)
    end = next((i for i, l in enumerate(lines) if i > start and re.search(r'swim for fitness|frequently asked', l, re.I)), len(lines))
    starts_with_day = re.compile(r'(?:mon|tues|wednes|thurs|fri|satur|sun)day\b', re.I)
    session = lambda o, c: {'name': 'Lido swim', 'open': o, 'close': c, 'booking': 'advised'}
    periods, daily = [], None
    for line in lines[start + 1:end]:
        if re.search(r'open daily from', line, re.I):
            daily = parse_date_range(re.split(r'open daily from', line, flags=re.I)[1], season_start)
            continue
        week_part = re.match(r'(weekdays|weekends)\b', line, re.I)
        if week_part and daily:
            days = [1, 2, 3, 4, 5] if week_part.group(1).lower() == 'weekdays' else [0, 6]
            periods.append({'from': daily['from'], 'to': daily['to'], 'days': days,
                            'sessions': [session(o, c) for o, c in time_ranges(line)]})
            continue
        if not starts_with_day.match(line):
            continue
        rng = parse_date_range(line, season_start)
        if rng:
            span, rest = rng, line[line.index(rng['text']) + len(rng['text']):]
        else:
            one = re.search(r'\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,}(?:\s+\d{4})?', line)
            day = parse_date(one.group(0), season_start) if one else None
            if not day:
                continue
            span, rest = {'from': day, 'to': day}, line[one.end():]
        times = time_ranges(rest)
        if times:
            periods.append({'from': span['from'], 'to': span['to'], 'sessions': [session(o, c) for o, c in times]})
    periods = [p for p in periods if p['sessions']]
    if not periods:
        raise ValueError('no dated opening hours found')
    if today > max(p['to'] for p in periods):
        note = 'Closed for the season. The lido opens in the summer, on dry days of 22°C or more.'
    else:
        note = 'Weather permitting - the lido only opens on dry days of 22°C or more.'
    return {'finchley': {'periods': periods, 'note': note}}


def beckenham_source(today):
    """PTP Coaching's homepage: Beckenham Place Park lake hours by season, e.g.
    "1st October - 15th November" then "Monday - 7:00am to 12:00pm" lines."""
    src = get(VENUES['beckenham']['source']['url'])
    lines = text_lines(src)
    start = next((i for i, l in enumerate(lines) if re.search(r'beckenham opening times', l, re.I)), -1)
    if start < 0:
        raise ValueError('no "Beckenham Opening Times" section')
    end = next((i for i, l in enumerate(lines) if i > start and re.search(r'opening times', l, re.I)), len(lines))
    day_line = re.compile(r'(mon|tues|wednes|thurs|fri|satur|sun)day\b\s*-?\s*(.*)', re.I)
    seasons = []
    for line in lines[start + 1:end]:
        dm = day_line.match(line)
        if dm:
            if seasons:
                seasons[-1]['days'].setdefault(DAYS.index(dm.group(1).lower()[:3]), []).extend(time_ranges(dm.group(2)))
            continue
        rng = parse_date_range(line, today)
        if rng:
            seasons.append({'from': rng['from'], 'to': rng['to'], 'days': {}})
        elif re.match(r'\d', line) and parse_date(line, today):
            # e.g. "15th November to Feb half term": the end isn't a date, so
            # treat it as running until further notice and re-check each run.
            seasons.append({'from': parse_date(line, today), 'to': None, 'days': {}})
    seasons = [s for s in seasons if any(s['days'].values())]
    if not seasons:
        raise ValueError('no seasons with opening hours found')
    for prev, nxt in zip(seasons, seasons[1:]):
        if prev['to'] and prev['to'] >= nxt['from']:
            prev['to'] = add_days(nxt['from'], -1)
    periods = []
    for s in seasons:
        to = s['to'] or max(add_days(today, STANDING_WEEKS * 7), s['from'])
        for day, times in sorted(s['days'].items()):
            if times:
                periods.append({'from': s['from'], 'to': to, 'days': [day],
                                'sessions': [{'name': 'Lake swim', 'open': o, 'close': c, 'booking': 'required'} for o, c in times]})
    result = {'periods': periods, 'note': 'Swims must be booked in advance with PTP Coaching. Hours change with the seasons.'}
    booking = href_matching(src, r'https://booking\.sportsguardian\.com/[^"\']*beckenham[^"\']*')
    if booking:
        result['bookingUrl'] = booking
    return {'beckenham': result}


# Each source fills in one or more venues.
SOURCES = [
    ('heath', ['mixed', 'ladies', 'mens', 'lido'], heath_source),
    ('brockwell', ['brockwell'], brockwell_source),
    ('londonfields', ['londonfields'], lambda today: better_source('londonfields', 'London Fields Lido', 'Lido swim', today)),
    ('oasis', ['oasis'], lambda today: better_source('oasis', 'Outdoor Pool', 'Outdoor pool swim', today)),
    ('parkroad', ['parkroad'], park_road_source),
    ('tooting', ['tooting'], tooting_source),
    ('serpentine', ['serpentine'], serpentine_source),
    ('charlton', ['charlton'], lambda today: better_source('charlton', 'Outdoor Pool', 'Outdoor pool swim', today)),
    ('hampton', ['hampton'], hampton_source),
    ('finchley', ['finchley'], finchley_source),
    ('beckenham', ['beckenham'], beckenham_source),
]

# --- checks & output ----------------------------------------------------------

def validate(venue_id, entry):
    time_re, date_re = re.compile(r'\d{2}:\d{2}'), re.compile(r'\d{4}-\d{2}-\d{2}')
    for p in entry['periods']:
        if not date_re.fullmatch(p['from']) or not date_re.fullmatch(p['to']) or p['from'] > p['to']:
            raise ValueError(f"{venue_id}: bad period {p['from']}..{p['to']}")
        for s in p['sessions']:
            if not time_re.fullmatch(s['open']) or not time_re.fullmatch(s['close']) or s['open'] >= s['close']:
                raise ValueError(f"{venue_id}: bad session {s['open']}-{s['close']}")
            last = s.get('lastEntry')
            if last and (not time_re.fullmatch(last) or last < s['open'] or last > s['close']):
                raise ValueError(f'{venue_id}: bad last entry {last}')


def trim(periods, today):
    """Drop periods that have ended or start beyond the horizon."""
    horizon = add_days(today, HORIZON_DAYS)
    return [p for p in periods if p['to'] >= today and p['from'] <= horizon]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Update data/timetables.json from the venues’ official pages.')
    parser.add_argument('--only', help='comma-separated source or venue ids, e.g. heath,brockwell')
    parser.add_argument('--dry-run', action='store_true', help='print the result instead of writing it')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT, help='where to write the JSON')
    args = parser.parse_args(argv)
    only = [x for x in (args.only or '').split(',') if x] or None

    today = london_today()
    now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    try:
        previous = json.loads(args.out.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {'venues': {}}
    old_venues = previous.get('venues') or {}

    venues, failures = {}, []
    for source_id, venue_ids, run in SOURCES:
        if only and source_id not in only and not any(v in only for v in venue_ids):
            for vid in venue_ids:
                if vid in old_venues:
                    venues[vid] = old_venues[vid]
            continue
        try:
            result = run(today)
            for vid in venue_ids:
                found = result.get(vid)
                if found is None:
                    raise ValueError(f'no data for {vid}')
                entry = {**VENUES[vid], **found, 'periods': trim(found['periods'], today)}
                validate(vid, entry)
                last = max((p['to'] for p in entry['periods']), default=None)
                venues[vid] = {**entry, 'publishedUntil': last, 'status': 'ok', 'checkedAt': now}
                extra = (f', published until {last}' if last else '') + (f" - {entry['note']}" if entry.get('note') else '')
                print(f"  ok    {vid:<13} {len(entry['periods']):>3} periods{extra}")
        except Exception as err:  # noqa: BLE001 - one broken page must not stop the rest
            failures.append(source_id)
            print(f'  FAIL  {source_id:<13} {err}', file=sys.stderr)
            for vid in venue_ids:
                old = old_venues.get(vid)
                if old:
                    venues[vid] = {**old, **VENUES[vid], 'periods': trim(old.get('periods') or [], today),
                                   'status': 'stale', 'error': str(err)}

    output = {'generatedAt': now, 'venues': venues}
    text = json.dumps(output, indent=2, ensure_ascii=False) + '\n'
    if args.dry_run:
        print(text, end='')
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding='utf-8')
        print(f'wrote {args.out}')
    if failures:
        print(f"{len(failures)} source(s) failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
