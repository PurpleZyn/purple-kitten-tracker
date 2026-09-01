# Purple's Kitten Tracker

This is a free, once-per-day Torn profile leaderboard.

It does four things:

1. Reads your Torn **Item Receive** logs.
2. Counts only **Kitten Plushies**.
3. Groups them by the player who directly sent them to you.
4. Creates `docs/kittens.png`, which GitHub Pages hosts at a permanent public URL.

## Important

**NEVER paste your Torn API key into any file in this folder.**

Your API key belongs only in:

`GitHub repository > Settings > Secrets and variables > Actions > TORN_API_KEY`

The repository can be public because the API key is not stored in the repository.

The generated donor totals and image are public. That is intentional because the image is meant for a public Torn profile.

## Torn IDs used

- Item Receive log: `4103`
- Kitten Plushie item: `215`

## First run

The first manual GitHub Actions run performs the historical import.

The tracker requests Item Receive logs in chunks, follows Torn pagination, filters those logs to item 215, stores each matching unique Torn log ID, and then resolves contributor names.

`data/events.json` is the permanent ledger. Because every send is keyed by its unique Torn log ID, the same send cannot be counted twice.

## Daily run

The workflow runs once each day at approximately **00:07 TCT (UTC)**.

It deliberately runs seven minutes after reset because GitHub warns that scheduled workflows can be delayed during the first minute of an hour.

Every daily run re-reads a small overlap of time. Unique log IDs prevent duplicate counting.

## Files you may want to edit later

### `config.json`

You can change:

- title
- subtitle
- number of displayed contributors (`top_n`)
- image width and height

Do not change the item/log IDs unless Torn itself changes them.

### `data/names.json`

Normally names are filled automatically through Torn's public basic-profile endpoint.

If a name ever fails to resolve, you can manually replace:

```json
"1234567": "Player 1234567"
```

with:

```json
"1234567": "ActualName"
```

## Resetting the tracker

If you ever intentionally want to rebuild the historical ledger from Torn:

1. Replace `data/events.json` with `{}`.
2. Replace `data/names.json` with `{}` if you also want names rebuilt.
3. Replace `data/state.json` with:

```json
{
  "initialized": false,
  "last_checked": 0,
  "last_updated_display": "Not synced yet"
}
```

4. Commit the changes.
5. Go to **Actions > Update kitten leaderboard > Run workflow**.

## Security

This personal tool uses your API key only inside GitHub Actions to make Torn API requests.

The workflow does not print the key, save the key to disk, add it to the generated image, or commit it to the repository.
