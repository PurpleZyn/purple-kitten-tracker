import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
CONFIG_PATH = ROOT / "config.json"
EVENTS_PATH = DATA_DIR / "events.json"
NAMES_PATH = DATA_DIR / "names.json"
STATE_PATH = DATA_DIR / "state.json"
IMAGE_PATH = DOCS_DIR / "kittens.png"
INDEX_PATH = DOCS_DIR / "index.html"
TEMPLATE_PATH = ROOT / "leaderboard_template.png"

TORN_V2 = "https://api.torn.com/v2"
COMMENT = "purple-kitten-tracker"
MIN_SECONDS_BETWEEN_CALLS = 0.70


def read_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")
    temp.replace(path)


def load_config():
    return read_json(
        CONFIG_PATH,
        {
            "title": "PURPLE'S KITTEN ENABLERS",
            "subtitle": "All-time direct Kitten Plushies sent",
            "kitten_item_id": 215,
            "item_receive_log_id": 4103,
            "top_n": 10,
            "width": 1000,
            "height": 600,
        },
    )


class TornClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "PurpleKittenTracker/1.0 (+personal Torn profile tracker)",
                "Accept": "application/json",
            }
        )
        self.last_call = 0.0

    def _throttle(self):
        wait = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)

    def _with_key(self, url):
        parsed = urlparse(url)
        q = dict(parse_qsl(parsed.query, keep_blank_values=True))
        q.setdefault("key", self.api_key)
        q.setdefault("comment", COMMENT)
        return urlunparse(parsed._replace(query=urlencode(q)))

    def get_json(self, url, params=None):
        self._throttle()
        if params:
            params = dict(params)
            params["key"] = self.api_key
            params.setdefault("comment", COMMENT)
            response = self.session.get(url, params=params, timeout=45)
        else:
            response = self.session.get(self._with_key(url), timeout=45)
        self.last_call = time.monotonic()
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"Torn API error {err.get('code')}: {err.get('error')}"
            )
        return data

    def logs_page(self, log_id, *, from_ts=None, to_ts=None, limit=100):
        params = {"log": str(log_id), "limit": limit}
        if from_ts is not None:
            params["from"] = int(from_ts)
        if to_ts is not None:
            params["to"] = int(to_ts)
        return self.get_json(f"{TORN_V2}/user/log", params=params)

    def player_name(self, player_id):
        data = self.get_json(f"{TORN_V2}/user/{int(player_id)}/basic")
        profile = data.get("profile", {})
        return profile.get("name") or f"Player {player_id}"


def normalize_logs(data):
    """
    Torn API v2 currently returns:
      {"log": [{"id": "...", "timestamp": ..., "details": {...}, "data": {...}}, ...]}

    Older/v1-shaped results used:
      {"log": {"LOG_ID": {"log": 4103, "timestamp": ..., "data": {...}}, ...}}

    Supporting both makes this tracker less brittle if Torn changes compatibility behavior.
    """
    raw = data.get("log", []) if isinstance(data, dict) else []
    out = []

    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            details = entry.get("details") or {}
            out.append(
                {
                    "id": str(entry.get("id", "")),
                    "timestamp": int(entry.get("timestamp", 0) or 0),
                    "log_type": details.get("id"),
                    "title": details.get("title", ""),
                    "data": entry.get("data") or {},
                }
            )
    elif isinstance(raw, dict):
        for log_id, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            out.append(
                {
                    "id": str(log_id),
                    "timestamp": int(entry.get("timestamp", 0) or 0),
                    "log_type": entry.get("log"),
                    "title": entry.get("title", ""),
                    "data": entry.get("data") or {},
                }
            )
    return out


def quantity_for_item(items, wanted_id):
    wanted_id = int(wanted_id)

    if isinstance(items, dict):
        for key, value in items.items():
            try:
                item_id = int(key)
            except (TypeError, ValueError):
                continue
            if item_id != wanted_id:
                continue

            if isinstance(value, bool):
                return int(value)
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
            if isinstance(value, list) and value:
                try:
                    return int(value[0])
                except (TypeError, ValueError):
                    return 0
            if isinstance(value, dict):
                for qty_key in ("quantity", "qty", "amount"):
                    if qty_key in value:
                        try:
                            return int(value[qty_key])
                        except (TypeError, ValueError):
                            return 0

    if isinstance(items, list):
        total = 0
        for item in items:
            if not isinstance(item, dict):
                continue

            item_id = item.get("id", item.get("item_id"))
            if isinstance(item.get("item"), dict):
                item_id = item["item"].get("id", item_id)

            try:
                item_id = int(item_id)
            except (TypeError, ValueError):
                continue

            if item_id != wanted_id:
                continue

            qty = item.get("quantity", item.get("qty", item.get("amount", 1)))
            try:
                total += int(qty)
            except (TypeError, ValueError):
                pass
        return total

    return 0


def sender_from_payload(payload):
    sender = payload.get("sender")
    if isinstance(sender, dict):
        sender_id = sender.get("id") or sender.get("player_id")
        sender_name = sender.get("name")
        return sender_id, sender_name
    return sender, None


def extract_kitten_event(entry, config):
    if not entry.get("id"):
        return None

    log_type = entry.get("log_type")
    title = str(entry.get("title", "")).lower()
    wanted_log = int(config["item_receive_log_id"])

    if log_type is not None:
        try:
            if int(log_type) != wanted_log:
                return None
        except (TypeError, ValueError):
            return None
    elif title != "item receive":
        return None

    payload = entry.get("data") or {}
    sender_id, sender_name = sender_from_payload(payload)
    if sender_id is None:
        return None

    qty = quantity_for_item(payload.get("items"), config["kitten_item_id"])
    if qty <= 0:
        return None

    return {
        "timestamp": int(entry.get("timestamp", 0)),
        "sender_id": str(sender_id),
        "quantity": int(qty),
        **({"sender_name": sender_name} if sender_name else {}),
    }


def add_page_events(page, events, names, config):
    added_events = 0
    added_kittens = 0
    timestamps = []

    for entry in normalize_logs(page):
        if entry["timestamp"]:
            timestamps.append(entry["timestamp"])

        event = extract_kitten_event(entry, config)
        if not event:
            continue

        event_id = entry["id"]
        if event_id in events:
            continue

        events[event_id] = event
        if event.get("sender_name"):
            names[event["sender_id"]] = event["sender_name"]
        added_events += 1
        added_kittens += event["quantity"]

    return added_events, added_kittens, timestamps


def next_link(page):
    try:
        return page["_metadata"]["links"]["next"]
    except (KeyError, TypeError):
        return None


def full_history_sync(client, events, names, config):
    """
    Initial import. Starts with the newest Item Receive logs and follows Torn's
    own next-page links backward through available history.

    A timestamp fallback is included for compatibility if a response does not
    provide pagination links.
    """
    total_events = 0
    total_kittens = 0
    seen_urls = set()
    page_number = 0

    page = client.logs_page(config["item_receive_log_id"], limit=100)

    while True:
        page_number += 1
        added_events, added_kittens, timestamps = add_page_events(
            page, events, names, config
        )
        total_events += added_events
        total_kittens += added_kittens

        logs = normalize_logs(page)
        print(
            f"History page {page_number}: {len(logs)} Item Receive logs, "
            f"{added_events} kitten sends, {added_kittens:,} kittens."
        )

        link = next_link(page)
        if link and link not in seen_urls:
            seen_urls.add(link)
            page = client.get_json(link)
            continue

        # Compatibility fallback: if Torn gives a full page but no link,
        # request the next older chunk by timestamp.
        if len(logs) >= 100 and timestamps:
            oldest = min(timestamps)
            fallback_key = f"to:{oldest - 1}"
            if fallback_key in seen_urls:
                break
            seen_urls.add(fallback_key)
            page = client.logs_page(
                config["item_receive_log_id"], to_ts=oldest - 1, limit=100
            )
            continue

        break

    return total_events, total_kittens


def incremental_sync(client, events, names, state, config):
    last_checked = int(state.get("last_checked", 0) or 0)
    # Re-read two minutes of overlap. Event IDs prevent double counting.
    from_ts = max(0, last_checked - 120) if last_checked else None

    page = client.logs_page(
        config["item_receive_log_id"], from_ts=from_ts, limit=100
    )

    total_events = 0
    total_kittens = 0
    seen_urls = set()

    while True:
        added_events, added_kittens, _ = add_page_events(
            page, events, names, config
        )
        total_events += added_events
        total_kittens += added_kittens

        link = next_link(page)
        if not link or link in seen_urls:
            break
        seen_urls.add(link)
        page = client.get_json(link)

    return total_events, total_kittens


def resolve_missing_names(client, events, names):
    sender_ids = sorted({e["sender_id"] for e in events.values()})
    missing = [sender_id for sender_id in sender_ids if not names.get(sender_id)]

    for sender_id in missing:
        try:
            name = client.player_name(sender_id)
            names[sender_id] = name
            print(f"Resolved player {sender_id} -> {name}")
        except Exception as exc:
            print(f"Could not resolve player {sender_id}: {exc}")
            names[sender_id] = f"Player {sender_id}"


def donor_totals(events, names):
    totals = defaultdict(int)
    for event in events.values():
        totals[event["sender_id"]] += int(event["quantity"])

    rows = [
        {
            "id": sender_id,
            "name": names.get(sender_id, f"Player {sender_id}"),
            "quantity": qty,
        }
        for sender_id, qty in totals.items()
    ]
    rows.sort(key=lambda x: (-x["quantity"], x["name"].lower()))
    return rows


def find_font(bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def font(size, bold=False):
    path = find_font(bold=bold)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def fit_text(draw, text, max_width, starting_size, bold=False, min_size=12):
    size = starting_size
    while size > min_size:
        f = font(size, bold=bold)
        box = draw.textbbox((0, 0), text, font=f)
        if box[2] - box[0] <= max_width:
            return f
        size -= 1
    return font(min_size, bold=bold)


def draw_cat_marker(draw, cx, cy, scale=1.0):
    """Small programmatic kitten-head marker; no emoji/font dependency."""
    purple = (186, 117, 255)
    dark = (47, 25, 67)
    ear = int(7 * scale)
    r = int(13 * scale)

    # ears
    draw.polygon(
        [(cx-r+2, cy-r+5), (cx-r+ear+2, cy-r-5), (cx-r+ear+8, cy-r+6)],
        fill=purple,
    )
    draw.polygon(
        [(cx+r-2, cy-r+5), (cx+r-ear-2, cy-r-5), (cx+r-ear-8, cy-r+6)],
        fill=purple,
    )
    # head
    draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=purple)
    # eyes / nose
    eye_r = max(1, int(2 * scale))
    draw.ellipse((cx-int(5*scale)-eye_r, cy-int(2*scale)-eye_r,
                  cx-int(5*scale)+eye_r, cy-int(2*scale)+eye_r), fill=dark)
    draw.ellipse((cx+int(5*scale)-eye_r, cy-int(2*scale)-eye_r,
                  cx+int(5*scale)+eye_r, cy-int(2*scale)+eye_r), fill=dark)
    draw.polygon(
        [(cx, cy+int(3*scale)), (cx-int(2*scale), cy+int(6*scale)),
         (cx+int(2*scale), cy+int(6*scale))],
        fill=dark,
    )


def render_image(rows, state, config):
    """
    Draw ONLY live data over leaderboard_template.png.
    The template intentionally contains no baked-in names, quantities,
    total, or update timestamp.
    """
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            "leaderboard_template.png is missing from the repository root."
        )

    img = Image.open(TEMPLATE_PATH).convert("RGB")
    draw = ImageDraw.Draw(img)

    text = (247, 244, 252)
    muted = (190, 180, 205)
    purple = (151, 87, 225)
    purple_dark = (55, 31, 78)
    line = (81, 48, 109)
    gold = (236, 194, 86)
    silver = (207, 210, 222)
    bronze = (222, 145, 73)

    top_n = int(config.get("top_n", 10))
    shown = rows[:top_n]
    max_qty = max((r["quantity"] for r in shown), default=1)

    # ----- Static column labels inside the CLEAN panel -----
    header_font = font(17, bold=True)
    draw.text((222, 322), "RANK", font=header_font, fill=(199, 143, 247))
    draw.text((327, 322), "ENABLER", font=header_font, fill=(199, 143, 247))
    draw.text((1115, 322), "KITTEN PLUSHIES SENT", font=header_font, fill=(199, 143, 247))

    # ----- Ten rows, all guaranteed to stay inside the panel -----
    first_y = 369
    row_h = 40
    rank_x = 230
    name_x = 320
    bar_x1 = 565
    bar_x2 = 1195
    qty_right = 1388

    rank_font = font(23, bold=True)
    qty_font = font(22, bold=True)

    for idx in range(1, top_n + 1):
        cy = first_y + (idx - 1) * row_h

        # separator
        if idx > 1:
            draw.line((215, cy - 20, 1405, cy - 20), fill=line, width=1)

        # Empty rows remain clean if fewer than 10 contributors.
        if idx > len(shown):
            continue

        row = shown[idx - 1]

        rank_color = (
            gold if idx == 1
            else silver if idx == 2
            else bronze if idx == 3
            else muted
        )

        draw.text((rank_x, cy - 13), f"{idx}.", font=rank_font, fill=rank_color)

        # Name automatically shrinks rather than colliding with the bar.
        display_name = row["name"]
        name_font = fit_text(
            draw, display_name, max_width=225,
            starting_size=24, bold=True, min_size=15
        )
        draw.text((name_x, cy - 13), display_name, font=name_font, fill=text)

        # bar
        bar_y1 = cy - 9
        bar_y2 = cy + 9
        draw.rounded_rectangle(
            (bar_x1, bar_y1, bar_x2, bar_y2),
            radius=9,
            fill=purple_dark,
        )

        fill_w = int((row["quantity"] / max_qty) * (bar_x2 - bar_x1))
        fill_w = max(8, fill_w)

        draw.rounded_rectangle(
            (bar_x1, bar_y1, bar_x1 + fill_w, bar_y2),
            radius=9,
            fill=purple,
        )

        # cat marker is clamped INSIDE the bar region
        marker_x = min(bar_x2 - 14, max(bar_x1 + 14, bar_x1 + fill_w))
        draw_cat_marker(draw, marker_x, cy, scale=0.82)

        # quantity right-aligned
        qty_text = f"{row['quantity']:,}"
        qty_box = draw.textbbox((0, 0), qty_text, font=qty_font)
        qty_width = qty_box[2] - qty_box[0]
        draw.text(
            (qty_right - qty_width, cy - 13),
            qty_text,
            font=qty_font,
            fill=text,
        )

    # ----- Live total -----
    total = sum(r["quantity"] for r in rows)

    total_label_font = font(24, bold=True)
    draw.text((420, 830), "TOTAL SENT:", font=total_label_font, fill=(208, 149, 255))

    total_text = f"{total:,}"
    total_font = fit_text(
        draw, total_text, max_width=300,
        starting_size=43, bold=True, min_size=28
    )
    draw.text((420, 862), total_text, font=total_font, fill=text)

    kittens_font = font(27, bold=True)
    draw.text((420, 907), "KITTENS", font=kittens_font, fill=(190, 111, 255))

    # ----- Live update timestamp -----
    updated = state.get("last_updated_display", "Not synced yet")
    update_text = f"Updated: {updated}"
    update_font = fit_text(
        draw, update_text, max_width=330,
        starting_size=19, bold=False, min_size=13
    )
    draw.text((1147, 914), update_text, font=update_font, fill=(211, 163, 255))

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    img.save(IMAGE_PATH, "PNG", optimize=True)


def render_index(rows, state, config):
    total = sum(r["quantity"] for r in rows)
    updated = state.get("last_updated_display", "Not synced yet")
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{config.get("title", "Kitten Tracker")}</title>
<style>
body {{
  margin: 0; padding: 32px; background: #140f1d; color: #f7f4fc;
  font-family: system-ui, -apple-system, Segoe UI, sans-serif; text-align:center;
}}
img {{ max-width:100%; height:auto; border-radius:16px; }}
p {{ color:#b8b0c5; }}
</style>
</head>
<body>
<h1>{config.get("title", "Kitten Tracker")}</h1>
<img src="kittens.png" alt="Kitten contribution leaderboard">
<p>{total:,} kittens tracked · Updated {updated}</p>
</body>
</html>
"""
    INDEX_PATH.write_text(page, encoding="utf-8")


def main():
    api_key = os.environ.get("TORN_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "TORN_API_KEY is missing. Add it as a GitHub Actions repository secret."
        )

    config = load_config()
    events = read_json(EVENTS_PATH, {})
    names = read_json(NAMES_PATH, {})
    state = read_json(
        STATE_PATH,
        {"initialized": False, "last_checked": 0, "last_updated_display": "Not synced yet"},
    )

    client = TornClient(api_key)
    run_started = int(time.time())

    if not state.get("initialized"):
        print("FIRST RUN: importing all available Item Receive history...")
        added_events, added_kittens = full_history_sync(
            client, events, names, config
        )
        state["initialized"] = True
    else:
        print("DAILY RUN: checking for new Item Receive logs...")
        added_events, added_kittens = incremental_sync(
            client, events, names, state, config
        )

    resolve_missing_names(client, events, names)

    state["last_checked"] = run_started
    state["last_updated_display"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M TCT"
    )
    state["tracked_sends"] = len(events)
    state["tracked_kittens"] = sum(int(e["quantity"]) for e in events.values())

    write_json(EVENTS_PATH, events)
    write_json(NAMES_PATH, names)
    write_json(STATE_PATH, state)

    rows = donor_totals(events, names)
    render_image(rows, state, config)
    render_index(rows, state, config)

    print()
    print(f"New kitten sends this run: {added_events}")
    print(f"New kittens this run: {added_kittens:,}")
    print(f"All-time tracked kitten sends: {len(events)}")
    print(f"All-time tracked kittens: {state['tracked_kittens']:,}")
    print(f"Unique contributors: {len(rows)}")


if __name__ == "__main__":
    main()
