'use strict';

/* ---------------------------------------------------------------------------
 * Opening times come from data/timetables.json, written by
 * scripts/update_timetables.py from each venue's official page. Each venue
 * has periods (date ranges, optionally limited to some weekdays) holding its
 * sessions; see the top of that script for the exact shape.
 * ------------------------------------------------------------------------- */

const DATA_URL = 'data/timetables.json';

const CLOSING_SOON_MINUTES = 30;

// A closed venue shows "N hours M minutes to wait" only if it reopens within this long.
const COUNTDOWN_MAX_WAIT_MINUTES = 36 * 60;


/* --- time helpers --------------------------------------------------------- */

/** The current civil date and time in London, whatever the device is set to. */
function londonNow() {
  const parts = {};
  const fmt = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/London',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });
  for (const part of fmt.formatToParts(new Date())) parts[part.type] = part.value;
  return {
    iso: `${parts.year}-${parts.month}-${parts.day}`,
    minutes: +parts.hour * 60 + +parts.minute,
  };
}

/** A civil date held at midday UTC, so adding days never trips over DST. */
const toDate = (iso) => new Date(`${iso}T12:00:00Z`);

function addDays(iso, n) {
  const d = toDate(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** "18:15" -> minutes since midnight */
function minutesOf(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

function formatTime(minutes) {
  const h24 = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  const suffix = h24 < 12 ? 'am' : 'pm';
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  return m === 0 ? `${h12}${suffix}` : `${h12}:${String(m).padStart(2, '0')}${suffix}`;
}

function formatDuration(minutes) {
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  if (minutes < 60) return plural(minutes, 'minute');
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m === 0 ? plural(h, 'hour') : `${plural(h, 'hour')} ${plural(m, 'minute')}`;
}

/** "2 November" */
function formatDate(iso) {
  return toDate(iso).toLocaleDateString('en-GB', { timeZone: 'UTC', day: 'numeric', month: 'long' });
}

/** "today", "tomorrow", "on Saturday", or "on Saturday 4 April" for far dates. */
function describeDay(iso, daysAhead) {
  if (daysAhead === 0) return 'today';
  if (daysAhead === 1) return 'tomorrow';
  const date = toDate(iso);
  const weekday = date.toLocaleDateString('en-GB', { timeZone: 'UTC', weekday: 'long' });
  if (daysAhead < 7) return `on ${weekday}`;
  const full = date.toLocaleDateString('en-GB', { timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long' });
  return `on ${full}`;
}

/* --- timetables ----------------------------------------------------------- */

/**
 * The sessions at a venue on one date, earliest first, with times in minutes.
 * Every matching period adds its sessions, except that a period marked
 * `replace` (a bank holiday, say) stands in for the rest.
 */
function sessionsOn(venue, iso) {
  const dow = toDate(iso).getUTCDay();
  const matching = (venue.periods || []).filter((p) => p.from <= iso && iso <= p.to && (!p.days || p.days.includes(dow)));
  const replacing = matching.filter((p) => p.replace);
  const chosen = replacing.length ? [replacing[replacing.length - 1]] : matching;
  return chosen.flatMap((p) => p.sessions).map((s) => ({
    name: s.name,
    open: minutesOf(s.open),
    close: minutesOf(s.close),
    lastEntry: s.lastEntry ? minutesOf(s.lastEntry) : null,
    booking: s.booking || null,
  })).sort((a, b) => a.open - b.open);
}

/** When you can last get in: the published last entry, or closing time if none is given. */
const entryEnd = (session) => (session.lastEntry ?? session.close);

/** The session you could get into right now, if any. */
function currentSession(venue, now) {
  return sessionsOn(venue, now.iso).find((s) => now.minutes >= s.open && now.minutes < entryEnd(s)) || null;
}

/** The next session that starts after right now, within the published times. */
function nextSession(venue, now) {
  const until = venue.publishedUntil || now.iso;
  for (let i = 0; i <= 400; i++) {
    const iso = addDays(now.iso, i);
    if (iso > until) break;
    for (const session of sessionsOn(venue, iso)) {
      if (i === 0 && session.open <= now.minutes) continue;
      return { session, iso, daysAhead: i };
    }
  }
  return null;
}

/** First sentence of a venue's note, for compact lists. */
const shortNote = (note) => note.split(/(?<=\.)\s/)[0].replace(/\.$/, '');

/**
 * Everything the page shows about one venue at one moment:
 *   state     open | soon | ticket | closed   (drives the colours)
 *   verdict   the big word
 *   detail    the sentence under it
 *   countdown the plain line after it, e.g. "40 minutes to get in" (or null)
 *   note      compact summary for lists, e.g. "Last entry 2pm"
 *   booking   whether to offer the booking link
 */
function evaluate(venue, now) {
  const open = currentSession(venue, now);

  if (open) {
    const knowsLastEntry = open.lastEntry != null;
    const minutesLeft = entryEnd(open) - now.minutes;
    const countdown = knowsLastEntry ? `${formatDuration(minutesLeft)} to get in` : `${formatDuration(minutesLeft)} until it closes`;
    const note = knowsLastEntry ? `Last entry ${formatTime(open.lastEntry)}` : `Open until ${formatTime(open.close)}`;
    const advised = open.booking === 'advised' ? ' Booking ahead is recommended.' : '';

    if (minutesLeft <= CLOSING_SOON_MINUTES) {
      return {
        state: 'soon',
        verdict: 'Yes, but hurry',
        detail: knowsLastEntry
          ? `Last entry is at ${formatTime(open.lastEntry)} - ${formatDuration(minutesLeft)} from now. Everyone out of the water by ${formatTime(open.close)}.`
          : `It closes at ${formatTime(open.close)} - ${formatDuration(minutesLeft)} from now.`,
        countdown,
        note,
        booking: Boolean(open.booking),
      };
    }

    if (open.booking === 'required') {
      return {
        state: 'ticket',
        verdict: 'Yes, with a ticket',
        detail: knowsLastEntry
          ? `This session runs until last entry at ${formatTime(open.lastEntry)}. You need to book ahead.`
          : `This session runs until ${formatTime(open.close)}. You need to book ahead.`,
        countdown: null,
        note,
        booking: true,
      };
    }

    return {
      state: 'open',
      verdict: 'Yes',
      detail: knowsLastEntry
        ? `Open now - last entry ${formatTime(open.lastEntry)}, out of the water by ${formatTime(open.close)}.${advised}`
        : `Open now until ${formatTime(open.close)}.${advised}`,
      countdown,
      note,
      booking: Boolean(open.booking),
    };
  }

  const next = nextSession(venue, now);
  const parts = [];
  let countdown = null;
  let note;

  if (next) {
    const when = describeDay(next.iso, next.daysAhead);
    const at = formatTime(next.session.open);
    parts.push(`It opens again ${when} at ${at}.`);
    if (next.session.booking === 'required') parts.push('That is a bookable session.');
    // Explain a long wait caused by the season rather than the time of day.
    if (venue.note && next.daysAhead > 7) parts.push(venue.note);
    note = `Opens ${when} at ${at}`;

    const wait = next.daysAhead * 24 * 60 + next.session.open - now.minutes;
    if (wait <= COUNTDOWN_MAX_WAIT_MINUTES) countdown = `${formatDuration(wait)} to wait`;
  } else if (venue.publishedUntil && venue.publishedUntil < addDays(now.iso, 1)) {
    // We've run past the end of the venue's published timetable.
    parts.push(`No times are published after ${formatDate(venue.publishedUntil)} yet.`);
    if (venue.note) parts.push(venue.note);
    note = 'No times published yet';
  } else if (venue.note) {
    // Closed for the season, e.g. "Closed for the winter. It re-opens in spring 2027."
    parts.push(venue.note);
    note = shortNote(venue.note);
  } else {
    parts.push('No opening times are published yet.');
    note = 'No times published yet';
  }

  return {
    state: 'closed',
    verdict: 'No',
    detail: parts.join(' '),
    countdown,
    note,
    booking: Boolean(next && next.session.booking),
  };
}

const STATUS_LABEL = { open: 'Open', soon: 'Closing soon', ticket: 'Book ahead', closed: 'Closed' };

/* --- distance ------------------------------------------------------------- */

/** Great-circle distance in miles. */
function distanceMiles(a, b) {
  const rad = (deg) => (deg * Math.PI) / 180;
  const dLat = rad(b.lat - a.lat);
  const dLon = rad(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * 3958.8 * Math.asin(Math.sqrt(h));
}

function formatMiles(miles) {
  return miles < 0.1 ? '<0.1 mi' : `${miles.toFixed(1)} mi`;
}

/* --- remembered on this device -------------------------------------------- */

const VENUE_KEY = 'can-i-swim-yet:venue';
const MODE_KEY = 'can-i-swim-yet:mode';

function load(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private browsing — the choice just won't persist */
  }
}

/* --- app state ------------------------------------------------------------ */

const app = {
  venues: [],          // filled from DATA_URL
  loadError: false,
  // 'nearest' shows whichever spot is closest to the visitor (once we know
  // where they are); 'picked' shows the one they chose. Both the mode and the
  // spot last shown are remembered, so the next visit opens the same way.
  mode: load(MODE_KEY) === 'picked' ? 'picked' : 'nearest',
  venueId: load(VENUE_KEY),
  here: null,          // { lat, lon } once the visitor shares their location
  locating: false,
  locationError: null, // why "Nearest to me" couldn't find the visitor
  wantsNearest: false, // tapped "Nearest to me" and waiting for a location
};

// The nearby list shows spots within this distance; if there are none, the two
// nearest within NEARBY_FALLBACK_MILES; and nothing if even those are too far.
const NEARBY_MILES = 3;
const NEARBY_FALLBACK_MILES = 10;
const NEARBY_FALLBACK_COUNT = 2;

/** Venues with their distance from a point, nearest first. */
function byDistance(from, venues = app.venues) {
  return venues
    .map((venue) => ({ venue, miles: distanceMiles(from, venue) }))
    .sort((a, b) => a.miles - b.miles);
}

/** Is the page showing the spot nearest the visitor, rather than one they picked? */
const showingNearest = () => app.mode === 'nearest' && app.here !== null && app.venues.length > 0;

/** The spot closest to the visitor, with its distance. Needs app.here. */
const nearest = () => byDistance(app.here)[0];

function currentVenue() {
  if (showingNearest()) return nearest().venue;
  return app.venues.find((v) => v.id === app.venueId) || app.venues[0];
}

/** For the picker: nearest first when we know where the visitor is, else in list order. */
function sortedVenues() {
  return app.here ? byDistance(app.here) : app.venues.map((venue) => ({ venue, miles: null }));
}

/**
 * The other spots near a point, nearest first: those within NEARBY_MILES, or
 * failing that the closest few within NEARBY_FALLBACK_MILES.
 */
function nearbyVenues(current, from) {
  const others = byDistance(from, app.venues.filter((v) => v.id !== current.id));
  const close = others.filter((o) => o.miles <= NEARBY_MILES);
  if (close.length) return close;
  return others.filter((o) => o.miles <= NEARBY_FALLBACK_MILES).slice(0, NEARBY_FALLBACK_COUNT);
}

/** Show a spot the visitor chose, from the picker or the nearby list. */
function selectVenue(id) {
  app.mode = 'picked';
  app.venueId = id;
  save(MODE_KEY, 'picked');
  save(VENUE_KEY, id);
  render();
}

/** In "nearest" mode, remember which spot that is, so the next visit opens on
    it while the browser finds the visitor again. */
function rememberNearest() {
  if (!showingNearest()) return;
  app.venueId = nearest().venue.id;
  save(VENUE_KEY, app.venueId);
}

/** "Nearest to me" in the picker: switch to it, finding the visitor first if need be. */
function chooseNearest() {
  if (!app.here) {
    app.wantsNearest = true;
    requestLocation();
    return;
  }
  app.mode = 'nearest';
  save(MODE_KEY, 'nearest');
  rememberNearest();
  render();
  closePicker();
}

/**
 * Ask the browser for the visitor's position. It is only used here to measure
 * distances and is never stored or sent anywhere.
 */
function requestLocation() {
  if (!('geolocation' in navigator)) {
    locationFailed('Location isn’t available in this browser');
    return;
  }
  if (app.locating) return;
  app.locating = true;
  app.locationError = null;
  refresh();

  // No timeout: some browsers start the clock while their permission prompt is
  // still waiting for an answer, which would quietly give up on a slow "Allow".
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      app.here = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      app.locating = false;
      if (app.wantsNearest) {
        app.wantsNearest = false;
        chooseNearest();
      }
      rememberNearest();
      refresh();
    },
    (err) => locationFailed(err.code === 1
      ? 'Location is blocked for this site - allow it in your browser’s settings'
      : 'Couldn’t find where you are - tap to try again'),
    { maximumAge: 10 * 60 * 1000 },
  );
}

function locationFailed(reason) {
  app.locating = false;
  // Only explain when the visitor asked; the ask on page load fails quietly.
  if (app.wantsNearest) app.locationError = reason;
  app.wantsNearest = false;
  refresh();
}

/**
 * In "nearest" mode, ask where the visitor is as soon as the page loads (the
 * browser shows its own prompt the first time). In "picked" mode, only look if
 * location is already allowed, for the distances in the picker. Nothing is
 * asked if location is blocked for the site.
 */
async function startLocation() {
  let state = 'prompt';
  try {
    const permission = await navigator.permissions.query({ name: 'geolocation' });
    state = permission.state;
    // Allowing location later (e.g. from the address bar) takes effect straight away.
    permission.addEventListener('change', () => {
      if (permission.state === 'granted') requestLocation();
    });
  } catch {
    /* no Permissions API: treat it as not asked yet */
  }
  if (state === 'granted' || (state === 'prompt' && app.mode === 'nearest')) requestLocation();
}

/* --- rendering: page ------------------------------------------------------ */

const $ = (id) => document.getElementById(id);
const els = {
  venueButton: $('venue-button'),
  venueName: $('venue-name'),
  where: $('where'),
  verdict: $('verdict'),
  detail: $('detail'),
  countdown: $('countdown'),
  action: $('action'),
  nearby: $('nearby'),
  nearbyTitle: $('nearby-title'),
  nearbyList: $('nearby-list'),
  picker: $('picker'),
  pickerClose: $('picker-close'),
  search: $('picker-search'),
  pickerMeta: $('picker-meta'),
  pickerNearest: $('picker-nearest'),
  pickerList: $('picker-list'),
};

const SVG_NS = 'http://www.w3.org/2000/svg';

function svg(width, height, viewBox, children, className) {
  const node = document.createElementNS(SVG_NS, 'svg');
  node.setAttribute('width', width);
  node.setAttribute('height', height);
  node.setAttribute('viewBox', viewBox);
  node.setAttribute('aria-hidden', 'true');
  if (className) node.setAttribute('class', className);
  for (const [tag, attrs] of children) {
    const child = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) child.setAttribute(k, v);
    node.appendChild(child);
  }
  return node;
}

const externalIcon = () => svg('0.9em', '0.9em', '0 0 24 24', [
  ['path', { d: 'M7 17 L17 7 M9 7 H17 V15', fill: 'none', stroke: 'currentColor', 'stroke-width': '2.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }],
]);

const tickIcon = () => svg('20', '20', '0 0 24 24', [
  ['path', { d: 'M5 12.5 L9.5 17 L19 7.5', fill: 'none', stroke: 'currentColor', 'stroke-width': '3', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }],
], 'pick-tick');

const locateIcon = () => svg('14', '14', '0 0 24 24', [
  ['path', { d: 'M21 3 L3 10.5 L10.5 13.5 L13.5 21 Z', fill: 'currentColor' }],
], 'locate-icon');

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

/* --- rendering: favicon --------------------------------------------------- */

// The browser tab shows the answer too: a tile in the page's colour with a
// tick (open), a clock (closing soon) or a cross (closed). A ticketed session
// shows as open - the tab only says whether you can get in. Until there is an
// answer, the tab keeps the swimmer icon from index.html.
const FAVICON_STROKE = 'fill="none" stroke="#fff" stroke-linecap="round" stroke-linejoin="round"';
const FAVICON_GLYPHS = {
  open: `<path d="M9 16.5l4.5 4.5 9.5-10" ${FAVICON_STROKE} stroke-width="3.5"/>`,
  soon: `<circle cx="16" cy="16" r="8.5" ${FAVICON_STROKE} stroke-width="2.75"/><path d="M16 11v5.5l3.5 2.5" ${FAVICON_STROKE} stroke-width="2.75"/>`,
  closed: `<path d="M11 11l10 10M21 11L11 21" ${FAVICON_STROKE} stroke-width="3.5"/>`,
};
const faviconLink = document.querySelector('link[rel="icon"]');
const defaultFavicon = faviconLink.href;
let faviconShown;

function setFavicon(state) {
  const shown = state === 'ticket' ? 'open' : state;
  if (shown === faviconShown) return; // render() runs every 30 seconds; only swap on a change
  faviconShown = shown;

  const glyph = FAVICON_GLYPHS[shown];
  if (!glyph) {
    faviconLink.href = defaultFavicon;
    return;
  }
  const colour = getComputedStyle(document.documentElement).getPropertyValue(`--state-${shown}`).trim();
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="${colour}"/>${glyph}</svg>`;
  faviconLink.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function render() {
  if (!app.venues.length) {
    document.body.className = 'state-loading';
    setFavicon(null);
    els.verdict.textContent = app.loadError ? '?' : '';
    els.detail.textContent = app.loadError ? 'Couldn’t load the opening times. Check your connection and reload the page.' : '';
    els.nearby.hidden = true;
    return;
  }

  const venue = currentVenue();
  const now = londonNow();
  const result = evaluate(venue, now);

  document.body.className = `state-${result.state}` + (els.picker.open ? ' picker-open' : '');
  setFavicon(result.state);

  els.venueName.textContent = venue.name;
  els.venueButton.setAttribute('aria-label', `${venue.name} - choose a different swimming spot`);

  // Under the times: how far away it is, whenever we know where the visitor is.
  els.where.replaceChildren();
  if (app.here) {
    els.where.append(locateIcon(), `${formatMiles(distanceMiles(app.here, venue))} away`);
  } else if (app.mode === 'nearest' && app.locating) {
    els.where.append(locateIcon(), 'Finding where you are…');
  }
  els.where.hidden = !els.where.firstChild;

  els.verdict.textContent = result.verdict;
  els.verdict.classList.toggle('long', result.verdict.length > 3);
  els.detail.textContent = result.detail;
  els.countdown.textContent = result.countdown || '';

  els.action.replaceChildren();
  if (result.booking && venue.bookingUrl) {
    const link = el('a');
    link.href = venue.bookingUrl;
    link.append('Book a session', externalIcon());
    els.action.appendChild(link);
  }

  // Places near the visitor when this is their nearest spot; otherwise places
  // near the spot itself. Distances are always from the visitor, nearest to
  // them first, and only shown when we know where they are.
  const fromVisitor = showingNearest();
  els.nearbyTitle.textContent = fromVisitor ? 'Also near you' : `Near ${venue.name}`;
  const nearby = nearbyVenues(venue, fromVisitor ? app.here : venue).map(({ venue: v }) => ({
    venue: v,
    miles: app.here ? distanceMiles(app.here, v) : null,
  }));
  if (app.here) nearby.sort((a, b) => a.miles - b.miles);
  const rows = nearby.map(({ venue: v, miles }) => {
    const status = evaluate(v, now);
    const button = el('button', 'near-row');
    button.type = 'button';
    button.dataset.id = v.id;
    button.append(
      el('span', 'near-name', v.name),
      el('span', `status-pill state-${status.state}`, STATUS_LABEL[status.state]),
    );
    if (miles != null) button.append(el('span', 'near-dist', formatMiles(miles)));
    const li = el('li');
    li.appendChild(button);
    return li;
  });
  els.nearbyList.replaceChildren(...rows);
  els.nearby.hidden = rows.length === 0;

}

/* --- rendering: picker ---------------------------------------------------- */

/** Lower-case, drop accents and apostrophes, so "mens" finds "Men’s". */
function fold(text) {
  return text.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[’'`]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
}

/** Does `text` contain the (folded) query starting at the beginning of a word? */
function startsAWord(text, q) {
  return ` ${fold(text)}`.includes(` ${q}`);
}

/** Search from the start of words, so "mens" finds Men’s but not "womens". */
function matches(venue, query) {
  const q = fold(query);
  if (!q) return true;
  return startsAWord([venue.name, ...(venue.keywords || [])].join(' '), q);
}

/** The venue name, with the typed text underlined when it appears literally. */
function nameWithMatch(label, query) {
  const wrap = el('span');
  const q = query.trim().toLowerCase();
  const i = q ? label.toLowerCase().indexOf(q) : -1;
  if (i < 0) {
    wrap.textContent = label;
  } else {
    wrap.append(label.slice(0, i), el('mark', null, label.slice(i, i + q.length)), label.slice(i + q.length));
  }
  return wrap;
}

/** "Nearest to me", above the list: ticked when that's what the page shows. */
function nearestEntry(now) {
  const button = el('button', 'pick-item nearest-entry');
  button.type = 'button';
  button.dataset.nearest = '';

  const name = el('span', 'pick-name');
  name.appendChild(el('span', null, 'Nearest to me'));

  const meta = el('span', 'pick-meta');
  meta.appendChild(locateIcon());
  if (app.here) {
    const { venue, miles } = nearest();
    meta.append(`Right now that’s ${venue.name} · ${formatMiles(miles)} · ${STATUS_LABEL[evaluate(venue, now).state]}`);
  } else {
    button.classList.add('off');
    meta.append(app.locating ? 'Finding where you are…' : app.locationError || 'Needs your location - tap to turn it on');
  }

  const text = el('span', 'pick-text');
  text.append(name, meta);
  button.appendChild(text);
  if (showingNearest()) {
    button.setAttribute('aria-current', 'true');
    button.appendChild(tickIcon());
  }
  return button;
}

function renderPicker() {
  if (!els.picker.open) return;
  const query = els.search.value;
  const searching = Boolean(query.trim());
  const now = londonNow();
  // Matches in the name come first, then keyword-only matches; each group
  // stays nearest-first.
  const q = fold(query);
  const inName = (venue) => (startsAWord(venue.name, q) ? 0 : 1);
  const hits = sortedVenues()
    .filter(({ venue }) => matches(venue, query))
    .sort((a, b) => inName(a.venue) - inName(b.venue));

  if (searching) {
    els.pickerMeta.textContent = hits.length === 1 ? '1 match' : `${hits.length} matches`;
  } else {
    els.pickerMeta.textContent = `${app.venues.length} places`;
  }

  // "Nearest to me", except while searching. Keep the focus on it if it's
  // redrawn while finding the visitor.
  const hadFocus = els.pickerNearest.contains(document.activeElement);
  els.pickerNearest.replaceChildren(...(searching ? [] : [nearestEntry(now)]));
  if (hadFocus && els.pickerNearest.firstChild) els.pickerNearest.firstChild.focus();

  // A spot is ticked only when it's one the visitor picked.
  const ticked = showingNearest() ? null : currentVenue().id;

  if (!hits.length) {
    const li = el('li');
    li.appendChild(el('p', 'picker-empty', `Nothing matches “${query.trim()}”.`));
    els.pickerList.replaceChildren(li);
    return;
  }

  const items = hits.map(({ venue, miles }) => {
    const status = evaluate(venue, now);
    const button = el('button', 'pick-item');
    button.type = 'button';
    button.dataset.id = venue.id;
    if (venue.id === ticked) button.setAttribute('aria-current', 'true');

    const name = el('span', 'pick-name');
    name.appendChild(nameWithMatch(venue.name, query));

    const meta = el('span', 'pick-meta');
    // "Open until 5pm" rather than "Open · Open until 5pm".
    const label = STATUS_LABEL[status.state];
    const bits = status.note.toLowerCase().startsWith(label.toLowerCase()) ? [status.note] : [label, status.note];
    if (miles != null) bits.push(formatMiles(miles));
    meta.append(el('span', `status-dot state-${status.state}`), bits.join(' · '));

    const text = el('span', 'pick-text');
    text.append(name, meta);
    button.appendChild(text);
    if (venue.id === ticked) button.appendChild(tickIcon());

    const li = el('li');
    li.appendChild(button);
    return li;
  });
  els.pickerList.replaceChildren(...items);
}

function openPicker() {
  if (!app.venues.length) return;
  els.search.value = '';
  els.picker.showModal();
  document.body.classList.add('picker-open');
  renderPicker();
  // Focus the search on keyboard/mouse devices; on touch screens wait for a tap
  // so the keyboard doesn't cover the list straight away.
  if (window.matchMedia('(pointer: fine)').matches) {
    els.search.focus();
  } else {
    (els.picker.querySelector('.pick-item[aria-current="true"]') || els.pickerClose).focus({ preventScroll: true });
  }
}

function closePicker() {
  if (els.picker.open) els.picker.close();
}

const refresh = () => {
  render();
  renderPicker();
};

/** Act on a choice in the picker: "Nearest to me" or a spot. */
function pick(item) {
  if ('nearest' in item.dataset) {
    chooseNearest();
    return;
  }
  selectVenue(item.dataset.id);
  closePicker();
}

/* --- events --------------------------------------------------------------- */

els.venueButton.addEventListener('click', openPicker);
els.pickerClose.addEventListener('click', closePicker);

els.picker.addEventListener('close', () => {
  document.body.classList.remove('picker-open');
  els.venueButton.focus();
});

els.search.addEventListener('input', renderPicker);

// Clicking a choice picks it; clicking anywhere else that isn't the search
// field or a button closes the list.
els.picker.addEventListener('click', (event) => {
  const item = event.target.closest('.pick-item');
  if (item) pick(item);
  else if (!event.target.closest('button, input, label, a')) closePicker();
});

// Arrow keys move between the search field and the choices; Enter in the
// search field picks the first one.
els.picker.addEventListener('keydown', (event) => {
  const items = [...els.picker.querySelectorAll('.pick-item')];
  const index = items.indexOf(document.activeElement);

  if (event.key === 'Enter' && document.activeElement === els.search) {
    event.preventDefault();
    if (items[0]) pick(items[0]);
  } else if (event.key === 'ArrowDown') {
    event.preventDefault();
    (items[index + 1] || items[0])?.focus();
  } else if (event.key === 'ArrowUp' && index >= 0) {
    event.preventDefault();
    (index === 0 ? els.search : items[index - 1]).focus();
  }
});

els.nearbyList.addEventListener('click', (event) => {
  const row = event.target.closest('.near-row');
  if (!row) return;
  selectVenue(row.dataset.id);
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  window.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'auto' });
});

/* --- start ---------------------------------------------------------------- */

async function loadTimetables() {
  try {
    const res = await fetch(DATA_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    app.venues = Object.entries(data.venues || {}).map(([id, v]) => ({ id, ...v }));
    if (!app.venues.length) throw new Error('no venues');
  } catch (err) {
    app.loadError = true;
    console.error('Could not load timetables', err);
  }
  // The location may have arrived first.
  rememberNearest();
  render();
}

render();
loadTimetables();
startLocation();
setInterval(render, 30000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) render();
});

// Exposed so the scheduling logic can be exercised from a test page; nothing
// in the app itself reads this.
window.CanISwimYet = {
  app, evaluate, sessionsOn, currentSession, nextSession, formatTime, formatDuration, describeDay, distanceMiles,
};
