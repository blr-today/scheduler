# blr.today scheduler

Posts upcoming [blr.today](https://blr.today) events to Bluesky, evenly through the day.

- `events.blr.today` posts every event outside the website's *unwanted* calendar.
- Tag accounts (`indiranagar.blr.today`, `curated.blr.today`) repost the posts of events on
  their website calendar. Calendars are read live from `blr-today/website`.
- `lastcall.blr.today` replies once when ingest tags a posted event `LASTCALL`.
- Changes before an event starts (time, venue, cheapest price, cancelled, sold out) get one
  "✏️ Update" reply. Bluesky posts can't be edited.
- Every upcoming occurrence also gets a `community.lexicon.calendar.event` record, which is
  updated in place and links to the upstream listing with `rsvpExpected: false`.

## Accounts

Each account shares a website calendar page (`calendar:`), or tags and schema.org types
from `scheduler.yml` (`tags:`, `types:`). Events: wanted events in 14 days (2026-10-06).
Fediverse accounts are all planned, on `fedi.blr.today` once the server is up.

| Bluesky | Fediverse | Shares | Events |
|---|---|---|---|
| [events.blr.today](https://bsky.app/profile/events.blr.today) (live) | [@events](https://fedi.blr.today/events) | everything outside `cal/unwanted` | 291 |
| [curated.blr.today](https://bsky.app/profile/curated.blr.today) (live) | [@curated](https://fedi.blr.today/curated) | `cal/curated` (the homepage) | 154 |
| [indiranagar.blr.today](https://bsky.app/profile/indiranagar.blr.today) (live) | [@indiranagar](https://fedi.blr.today/indiranagar) | `cal/indiranagar` | 84 |
| [lastcall.blr.today](https://bsky.app/profile/lastcall.blr.today) (live) | [@lastcall](https://fedi.blr.today/lastcall) | Last Call replies | 5 |
| [cbd.blr.today](https://bsky.app/profile/cbd.blr.today) (live) | [@cbd](https://fedi.blr.today/cbd) | `cal/cbd` | 68 |
| [free.blr.today](https://bsky.app/profile/free.blr.today) (live) | [@free](https://fedi.blr.today/free) | tags `FREE` | 51 |
| [whitefield.blr.today](https://bsky.app/profile/whitefield.blr.today) (next) | [@whitefield](https://fedi.blr.today/whitefield) | `cal/whitefield` | 41 |
| [fitness.blr.today](https://bsky.app/profile/fitness.blr.today) (next) | [@fitness](https://fedi.blr.today/fitness) | `cal/fitness` | 28 |
| [koramangala.blr.today](https://bsky.app/profile/koramangala.blr.today) (next) | [@koramangala](https://fedi.blr.today/koramangala) | `cal/koramangala` | 26 |
| — | [@workshops](https://fedi.blr.today/workshops) | type `EducationEvent` | 70 |
| — | [@budget](https://fedi.blr.today/budget) | tags `BUDGET` | 55 |
| — | [@sports](https://fedi.blr.today/sports) | type `SportsEvent` | 26 |
| — | [@north](https://fedi.blr.today/north) | `cal/north` | 21 |
| — | [@hsr](https://fedi.blr.today/hsr) | `cal/hsr` | 18 |
| — | [@underline](https://fedi.blr.today/underline) | `cal/underline` | 17 |
| — | [@books](https://fedi.blr.today/books) | `cal/bookstores` | 15 |
| — | [@food](https://fedi.blr.today/food) | type `FoodEvent` | 14 |
| — | [@music](https://fedi.blr.today/music) | type `MusicEvent` | 11 |
| — | [@bic](https://fedi.blr.today/bic) | `cal/bic` | 11 |
| — | [@film](https://fedi.blr.today/film) | type `ScreeningEvent` | 10 |
| — | [@jpnagar](https://fedi.blr.today/jpnagar) | `cal/jpnagar` | 9 |
| — | [@scigallery](https://fedi.blr.today/scigallery) | `cal/scigallery` | 9 |
| — | [@sabha](https://fedi.blr.today/sabha) | `cal/sabha` | 8 |
| — | [@jayanagar](https://fedi.blr.today/jayanagar) | `cal/jayanagar` | 3 |

## How a run works

`python -m scheduler run` runs every 5 minutes as a k3s CronJob:

1. Revalidates `https://blr.today/api/events.db` with its ETag. A new copy only replaces the
   old one after an SQLite integrity check, and data older than 48 hours stops the run.
2. Checks the ledger against the account's recent posts (see below).
3. Updates calendar records and posts corrections.
4. Between 10:00 and 19:00 IST, posts events that start 2–7 days from now. The queue goes by
   posting deadline (start minus 2 days) day, and `priority` calendars go first within a day.
   The gap between posts is the widest even gap that still gets every queued event out by its
   deadline, so busy weeks post more often. Under 5 minutes, a run posts several (max 20).
   Corrections, reposts and Last Calls keep following every posted event until it starts.
5. Catches up on missing reposts and Last Calls.

## Never posting twice

The ledger at `$SCHEDULER_STATE/ledger/bluesky.json`, on a persistent volume, decides what
has been posted. The run refuses to post, and exits 2, when:

- there is no ledger but the account already has event posts. Run `scheduler adopt bluesky`
  once, deliberately. A truly empty account starts a ledger by itself.
- the account has event posts from the last 30 days that the ledger doesn't know (a stale or
  restored ledger).
- another run holds the state lock.
- `events.db` is more than 48 hours old.

Each post is written to the ledger as *pending* before the API call. After a crash, the next
run adopts the post if it reached the account, or otherwise counts a failed attempt. It gives
up on an event after 3 failed attempts.

## Running it

```
uv run --frozen pytest -q
export BLUESKY_APP_PASSWORDS='{"events.blr.today": "xxxx-xxxx-xxxx-xxxx", ...}'
uv run --frozen python -m scheduler --state state --dry-run run
uv run --frozen python -m scheduler --state state adopt bluesky
```

`--dry-run` prints every write instead of making it; `--website` reads a local website checkout.

## Layout

- `scheduler/shared/`: events, calendars, post text, corrections, pacing, ledger,
  publishing loop and database fetch. It knows nothing about any platform.
- `scheduler/bluesky/`: XRPC client, record builders and the Bluesky feed.
- `scheduler.yml`: accounts, and the calendar, tags or types each one shares.

## Images

Each tested push to `main` is pushed as `ghcr.io/blr-today/scheduler:<run number>`. Run
numbers only ever increase, and the deploy in `nebula` pins one.
