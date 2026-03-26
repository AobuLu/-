import requests
import datetime as dt
import hashlib
from zoneinfo import ZoneInfo
from pathlib import Path

TZ = ZoneInfo("Asia/Shanghai")
BASE = "https://y.saoju.net/yyj/api/search_day/"

ARTISTS = {
    "977": "赵奕然",
    "1779": "庞东轩",
}

# ===== 增量策略 =====
LOOKBACK_DAYS = 2        # 向前补 2 天
FUTURE_DAYS = 365       # 向后一年

DURATION_HOURS = 2
TIMEOUT = 20

OUT = Path("docs")
OUT.mkdir(exist_ok=True)


def make_uid(*parts: str) -> str:
    raw = "|".join([str(p) for p in parts]).encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + "@saoju"


def escape_ics(s: str) -> str:
    if s is None:
        return ""
    return (
        str(s).replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fetch_day(date_str: str):
    r = requests.get(BASE, params={"date": date_str}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("show_list", []) or []


def to_dt_local(d, t):
    h, m = t.split(":")
    return dt.datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=TZ)


def session_label(t):
    if t in ("14:00", "14:30"):
        return "午"
    if t in ("19:00", "19:30"):
        return "晚"
    return ""


def normalize_cast(show):
    out = []
    for c in show.get("cast", []):
        a = (c.get("artist") or "").strip()
        r = (c.get("role") or "").strip()
        if a:
            out.append((a, r))
    return out


def build_desc(main_artist, main_role, city, theatre, cast_pairs):
    lines = [
        f"演员：{main_artist}",
        f"角色：{main_role}" if main_role else "",
        "",
        "同场演员："
    ]

    for a, r in cast_pairs:
        if r:
            lines.append(f"- {a}（{r}）")
        else:
            lines.append(f"- {a}")

    lines += ["", f"城市：{city}", f"剧院：{theatre}"]

    return "\n".join([x for x in lines if x])


def build_event(artist, role, d, show):
    start = to_dt_local(d, show["time"])
    end = start + dt.timedelta(hours=DURATION_HOURS)

    musical = show.get("musical", "")
    city = show.get("city", "")
    theatre = show.get("theatre", "")
    time_str = show.get("time", "")

    label = session_label(time_str)

    parts = [musical]
    if label:
        parts.append(label)
    parts.append(artist)
    if role:
        parts.append(role)

    summary = "｜".join(parts)

    cast_pairs = normalize_cast(show)

    uid = make_uid(
        artist, d.isoformat(), time_str, musical, theatre, role
    )

    return {
        "uid": uid,
        "start": start,
        "end": end,
        "summary": summary,
        "location": f"{city} {theatre}",
        "desc": build_desc(artist, role, city, theatre, cast_pairs),
    }


def parse_existing(path):
    if not path.exists():
        return [], set()

    text = path.read_text(encoding="utf-8")
    events = []
    seen = set()

    blocks = text.split("BEGIN:VEVENT")[1:]
    for b in blocks:
        uid = b.split("UID:")[1].split("\n")[0].strip()
        seen.add(uid)
        events.append("BEGIN:VEVENT" + b)

    return events, seen


def write_ics(path, events):
    now = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
    ]

    for e in events:
        lines.append(e)

    lines.append("END:VCALENDAR")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    today = dt.date.today()
    start = today - dt.timedelta(days=LOOKBACK_DAYS)
    end = today + dt.timedelta(days=FUTURE_DAYS)

    for aid, name in ARTISTS.items():
        path = OUT / f"artist_{aid}.ics"

        existing_events, seen = parse_existing(path)
        new_events = []

        total = (end - start).days + 1

        for i in range(total):
            d = start + dt.timedelta(days=i)

            if i % 50 == 0:
                print(f"{name} [{i}/{total}] {d}")

            try:
                shows = fetch_day(d.isoformat())
            except:
                continue

            for show in shows:
                for c in show.get("cast", []):
                    if c.get("artist") == name:
                        ev = build_event(name, c.get("role"), d, show)

                        if ev["uid"] in seen:
                            continue

                        seen.add(ev["uid"])

                        new_events.append(
                            "\n".join([
                                "BEGIN:VEVENT",
                                f"UID:{ev['uid']}",
                                f"DTSTAMP:{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
                                f"DTSTART;TZID=Asia/Shanghai:{ev['start'].strftime('%Y%m%dT%H%M%S')}",
                                f"DTEND;TZID=Asia/Shanghai:{ev['end'].strftime('%Y%m%dT%H%M%S')}",
                                f"SUMMARY:{escape_ics(ev['summary'])}",
                                f"LOCATION:{escape_ics(ev['location'])}",
                                f"DESCRIPTION:{escape_ics(ev['desc'])}",
                                "END:VEVENT"
                            ])
                        )

        print(f"{name} 新增 {len(new_events)} 场")

        write_ics(path, existing_events + new_events)


if __name__ == "__main__":
    main()
