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

## How a run works

`python -m scheduler run` runs every 5 minutes as a k3s CronJob:

1. Revalidates `https://blr.today/api/events.db` with its ETag. A new copy only replaces the
   old one after an SQLite integrity check, and data older than 48 hours stops the run.
2. Checks the ledger against the account's recent posts (see below).
3. Updates calendar records and posts corrections.
4. Between 10:00 and 19:00 IST, posts the next event once it is due. Posts are spaced as
   `(time left until 19:00) / (pending + 1)`, at least 7.5 minutes apart, and never later
   than 4 hours before the event starts.
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

`--dry-run` reads the real accounts and prints every write without making it. `--website`
reads calendar definitions from a local website checkout instead of GitHub.

## Layout

- `scheduler/shared/`: events, calendars, post text, corrections, pacing, ledger,
  publishing loop and database fetch. It knows nothing about any platform.
- `scheduler/bluesky/`: XRPC client, record builders and the Bluesky feed.
- `scheduler.yml`: accounts and calendars.

## Images

Every push to `main` that passes the tests is pushed as
`ghcr.io/blr-today/scheduler:<run number>`. The run number only ever increases, and the
deploy in `nebula` pins one.
