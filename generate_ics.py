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

START_DATE = dt.date(2018, 1, 1)
FUTURE_DAYS = 365

DURATION_HOURS = 2
TIMEOUT = 20

OUT = Path("docs")
OUT.mkdir(exist_ok=True)


def make_uid(*parts):
    raw = "|".join(map(str, parts)).encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + "@saoju"


def escape_ics(s):
    if not s:
        return ""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fetch_day(date_str):
    r = requests.get(BASE, params={"date": date_str}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("show_list", []) or []


def to_dt(d, t):
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


def build_desc(artist, role, city, theatre, cast_pairs):
    lines = [
        f"演员：{artist}",
        f"角色：{role}" if role else "",
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
    start = to_dt(d, show["time"])
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


def write_ics(path, events):
    now = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
    ]

    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{now}",
            f"DTSTART;TZID=Asia/Shanghai:{e['start'].strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Shanghai:{e['end'].strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{escape_ics(e['summary'])}",
            f"LOCATION:{escape_ics(e['location'])}",
            f"DESCRIPTION:{escape_ics(e['desc'])}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    today = dt.date.today()
    end_date = today + dt.timedelta(days=FUTURE_DAYS)

    events_by_artist = {aid: [] for aid in ARTISTS}
    seen = {aid: set() for aid in ARTISTS}

    total = (end_date - START_DATE).days + 1

    for i in range(total):
        d = START_DATE + dt.timedelta(days=i)

        if i % 50 == 0:
            print(f"[{i}/{total}] {d}")

        try:
            shows = fetch_day(d.isoformat())
        except:
            continue

        for show in shows:
            for c in show.get("cast", []):
                artist_in_show = c.get("artist")
                role = c.get("role")

                for aid, name in ARTISTS.items():
                    if artist_in_show == name:
                        ev = build_event(name, role, d, show)

                        if ev["uid"] in seen[aid]:
                            continue

                        seen[aid].add(ev["uid"])
                        events_by_artist[aid].append(ev)

    for aid, name in ARTISTS.items():
        evs = sorted(events_by_artist[aid], key=lambda x: x["start"])
        write_ics(OUT / f"artist_{aid}.ics", evs)
        print(f"{name} 共 {len(evs)} 场")


if __name__ == "__main__":
    main()
