# Can I swim yet?

A single-page static site that answers whether you can swim right now at a
London pond, lido or lake: the four Hampstead Heath spots, Brockwell, London
Fields, Oasis, Park Road, Tooting Bec, the Serpentine, Charlton, Hampton Pool,
Finchley and Beckenham Place Park lake.

- **Green** — open. Shows how long you have to get in (or until it closes, where
  the venue doesn't publish a last-entry time).
- **Yellow** — open, but last entry (or closing) is within 30 minutes.
- **Blue** — open, but the current session must be booked.
- **Red** — closed, and tells you when it next opens or why (e.g. closed for winter).

A **Book a session** button links to the venue's booking page whenever the
session needs or recommends booking.

All times are evaluated in `Europe/London` regardless of the visitor's device
timezone. The site has no build step and no dependencies.

## Nearest to you, or a spot you picked

The page works in one of two ways:

- **Nearest to you** (the default). The page shows whichever spot is closest
  to the visitor and lists **Also near you**.
- **A spot you picked**. The page shows that spot and lists **Near {spot}**:
  the other spots close to it.

Distances are only shown once we know where the visitor is, and are always
measured from them: "0.2 mi away" under the times, and in the nearby list
(sorted nearest to the visitor first) and the full list. Without the visitor's
location no distances are shown.

Picking a spot from the list, or tapping one in the nearby list, switches to
the second. **Nearest to me** at the top of the list switches back, asking for
the visitor's location first if we don't have it.

The nearby list shows other spots within 3 miles, nearest first. If none are
that close it shows the two nearest within 10 miles, and if there are none of
those either the section is hidden. The distances are `NEARBY_MILES` and
`NEARBY_FALLBACK_MILES` in `app.js`.

## Choosing a spot

Tap the underlined pond name to open a full-screen list: **Nearest to me**
first (showing which spot that is right now, or that it needs the visitor's
location), then every spot with its live status, nearest first once we know
where the visitor is. The current choice is ticked. The pond name's place in
the question becomes a search field, so you can just start typing ("ladies",
"mens", "lido", "kenwood" all work). Arrow keys move through the choices,
Enter picks the first one. Escape, **Close**, or clicking anywhere that isn't
a choice, the search field or a button closes the list.

Search also matches each spot's `keywords` (e.g. its area), so "hampstead" or
"heath" finds every Hampstead Heath spot. It matches from the start of words,
so "mens" finds the Men's Pond but not the "womens" keyword on the Ladies'
Pond. Spots whose name matches are listed first.

## Location and what's saved

Nothing leaves the visitor's browser.

- `localStorage` remembers which way the page works and the spot last shown,
  so the next visit opens the same way. In nearest mode that's the last
  nearest spot, which stays up while the browser finds the visitor again.
- In nearest mode the site asks for the visitor's location when the page loads
  (the browser shows its own prompt the first time, and the page says "Finding
  where you are…" under the times meanwhile). In picked mode it only looks if location is
  already allowed, to fill in distances in the list. Nothing is asked if
  location is blocked for the site.
- The position is used only to measure distances on the device and is never
  stored.
- If location is blocked or declined, the page shows the remembered spot (or
  the first in the list) as if it had been picked. **Nearest to me** in the
  list says it needs the visitor's location, asks again when tapped, and
  explains if the browser has blocked it. Allowing location later from the
  address bar takes effect straight away.

## Opening times: `data/timetables.json`

The site reads every venue's times from `data/timetables.json`. That file is
written by the updater, which reads each venue's official page:

| Venue | Source |
|---|---|
| Hampstead Heath ponds and Parliament Hill Lido | [City of London](https://www.cityoflondon.gov.uk/things-to-do/green-spaces/hampstead-heath/activities-at-hampstead-heath/swimming-at-hampstead-heath/swimming-opening-times) (plus the Mixed Pond page for its season) |
| Brockwell Lido | [Active Lambeth](https://active.lambeth.gov.uk/locations/brockwell-lido/) — weekly table |
| London Fields Lido, Oasis outdoor pool, Charlton Lido | [Better](https://www.better.org.uk/leisure-centre/london/hackney/london-fields-lido) — one week per page, read 4 weeks ahead |
| Finchley Lido | [Better](https://www.better.org.uk/leisure-centre/london/barnet/finchley-lido-leisure-centre/lido) — the lido page's dated summer hours |
| Park Road Lido | [Haringey Council](https://haringey.gov.uk/leisure-parks-culture/leisure-centres/park-road-leisure-centre-lido/facilities-park-road/lido-park-road) — standing hours, plus [GOV.UK bank holidays](https://www.gov.uk/bank-holidays.json) |
| Tooting Bec Lido | [South London Swimming Club](https://slsc.org.uk/opening-times/) — public season and hours |
| Serpentine Lido | [The Royal Parks](https://sportsandleisure.royalparks.org.uk/hyde-park/swimming) |
| Hampton Pool | [Hampton Pool](https://hamptonpool.co.uk/) — the homepage's weekly grid |
| Beckenham Place Park lake | [PTP Coaching](https://www.ptp.org.uk/) — hours by season |

Each venue's entry also records its `source` and when it was last checked
(`checkedAt`, `status`), though the page doesn't currently show them.

### Running the updater

```sh
python3 scripts/update_timetables.py              # update every venue
python3 scripts/update_timetables.py --only=heath,brockwell
python3 scripts/update_timetables.py --dry-run    # print the result, write nothing
python3 scripts/update_timetables.py --out=/path/to/timetables.json
```

It needs Python 3.9 or later and only the standard library. On GitHub it runs
every morning by itself (see below). Daily is plenty; Brockwell and Better
publish week by week, and Better only a few weeks ahead.

If a page can't be fetched or no longer looks the way its parser expects, that
venue keeps its previous times (marked `"status": "stale"` with the reason in
`"error"`), the other venues still update, and the script exits with code 1. The Serpentine parser only understands the "Closed for
winter" wording so far; when the page publishes summer times it will fail
loudly until it's taught the new format.

### Known approximations

- Venues other than the Heath don't publish last-entry times, so the site
  counts down to closing time for them.
- The Heath's summer session table is applied to every date range marked `*`
  on its page, although the page says bookable sessions run "from late May to
  early September".
- Tooting Bec's "6am or sunrise" is treated as 6am.
- Hampton Pool's lane and recreational swims are merged into the times the main
  pool is open; the learner pool (booking essential) is left out.
- Finchley Lido only lists some dates (the first weekend, May half term and the
  summer holidays); term-time weekends have no published hours, so they show as
  closed. It also only opens on dry days of 22°C or more.
- Beckenham's current season runs "to Feb half term", which isn't a date, so it
  is repeated 8 weeks ahead on each run until the page changes.
- Short-notice closures (weather, water quality, events) aren't on any of these
  pages and aren't reflected.

## Adding another venue

In `scripts/update_timetables.py`:

1. Add the venue to `VENUES` with its name, approximate `lat`/`lon` (a
   [postcodes.io](https://postcodes.io) lookup of its postcode is close
   enough), search `keywords` (e.g. area names), `bookingUrl` and `source`.
2. Write a source function that takes today's date, fetches the page and
   returns `{venue_id: {'periods': [...], 'note': ...}}`, and add it to
   `SOURCES`. Raise an exception if the page doesn't look as expected; the
   venue then keeps its previous times.

A period is `{ from, to, days?, replace?, sessions }` with dates as
`YYYY-MM-DD`, `days` as weekday numbers (0 = Sunday) and each session as
`{ name, open, lastEntry?, close, booking }`, times in `HH:MM` London time.
`close` is when swimmers leave the water; `booking` is `"required"`,
`"advised"` or `null`. The site needs no changes.

## Hosting on GitHub Pages

The site is at **https://caniswimyet.london/** (the custom domain set in
**Settings → Pages**; `www.caniswimyet.london` redirects there). DNS for both
domains is on Cloudflare; the domains are registered at Namecheap.
**pondhub.co.uk** (and `www.`) permanently redirects to caniswimyet.london,
keeping the path, using a Cloudflare redirect rule. It is published by GitHub Actions
(`.github/workflows/publish.yml`), with **Settings → Pages → Source** set to
**GitHub Actions**. The workflow:

- runs every day at 04:17 UTC (GitHub sometimes starts scheduled runs a little
  late), and whenever it's started by hand from the **Actions** tab with
  **Run workflow**: it runs the updater, commits `data/timetables.json` if it
  changed, and publishes the site;
- runs on every push to `main`, publishing the site as pushed without
  refreshing the times.

Only `index.html`, `app.js`, `styles.css` and `data/` are published. In the
published page the links to `app.js` and `styles.css` get a fingerprint of the
file (`app.js?v=3f9a1c02be`), so visitors' browsers fetch a file again as soon
as it changes rather than mixing a new page with an old cached script. If any
venue couldn't be updated, the site is still published with that venue's
previous times and the run is marked as failed, so GitHub emails whoever last
changed the workflow file. The failed run's "Refresh the opening times" step
says which venue and why.

GitHub turns off scheduled workflows in public repositories after 60 days
without activity. The daily data commits should keep it running; if it ever
stops, re-enable it from the **Actions** tab.

To run the updater somewhere else instead, call it from cron and publish the
updated file, e.g.:

```cron
15 5 * * * cd /path/to/can_I_swim_yet && python3 scripts/update_timetables.py
```
