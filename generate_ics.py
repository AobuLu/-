import requests
import datetime as dt
import hashlib
from zoneinfo import ZoneInfo
from pathlib import Path

TZ = ZoneInfo("Asia/Shanghai")
BASE = "https://y.saoju.net/yyj/api/search_day/"

# 只生成这两个演员的日历
ARTISTS = {
    "977": "赵奕然",
    "1779": "庞东轩",
}

# 历史起始时间（越早越全，越慢）
START_DATE = dt.date(2018, 1, 1)

# 未来扫描天数
FUTURE_DAYS = 365

# 每场演出时长（小时）
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
    s = str(s)
    return (
        s.replace("\\", "\\\\")
         .replace(";", "\\;")
         .replace(",", "\\,")
         .replace("\r\n", "\n")
         .replace("\r", "\n")
         .replace("\n", "\\n")
    )


def fetch_day(date_str: str):
    r = requests.get(BASE, params={"date": date_str}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("show_list", []) or []


def to_dt_local(date_obj: dt.date, hhmm: str) -> dt.datetime:
    h, m = hhmm.split(":")
    return dt.datetime(
        date_obj.year, date_obj.month, date_obj.day,
        int(h), int(m),
        tzinfo=TZ
    )


def session_label(hhmm: str) -> str:
    """根据开演时间判断 午 / 晚"""
    if hhmm in ("14:00", "14:30"):
        return "午"
    if hhmm in ("19:00", "19:30"):
        return "晚"
    return ""


def normalize_cast(show: dict):
    out = []
    for c in (show.get("cast") or []):
        a = (c.get("artist") or "").strip()
        r = (c.get("role") or "").strip()
        if a:
            out.append((a, r))
    return out


def build_description(main_artist: str, main_role: str, city: str, theatre: str, cast_pairs: list):
    lines = []
    lines.append(f"演员：{main_artist}")
    if main_role:
        lines.append(f"角色：{main_role}")

    lines.append("")
    lines.append("同场演员（本场完整卡司）：")

    main_line = f"- {main_artist}"
    if main_role:
        main_line += f"（{main_role}）"
    main_line += "  ← 本日历主角"
    lines.append(main_line)

    for a, r in cast_pairs:
        if a == main_artist:
            continue
        if r:
            lines.append(f"- {a}（{r}）")
        else:
            lines.append(f"- {a}")

    lines.append("")
    if city:
        lines.append(f"城市：{city}")
    if theatre:
        lines.append(f"剧院：{theatre}")

    return "\n".join(lines)


def build_event_for_artist(artist_name: str, role: str, d: dt.date, show: dict):
    start = to_dt_local(d, show["time"])
    end = start + dt.timedelta(hours=DURATION_HOURS)

    musical = (show.get("musical") or "").strip()
    city = (show.get("city") or "").strip()
    theatre = (show.get("theatre") or "").strip()
    time_str = (show.get("time") or "").strip()

    label = session_label(time_str)
    cast_pairs = normalize_cast(show)

    # ===== 标题：剧名｜午/晚｜演员｜角色 =====
    parts = [musical]
    if label:
        parts.append(label)
    parts.append(artist_name)
    if role:
        parts.append(role)
    summary = "｜".join(parts)

    location = f"{city} {theatre}".strip()

    desc = build_description(
        main_artist=artist_name,
        main_role=role,
        city=city,
        theatre=theatre,
        cast_pairs=cast_pairs
    )

    uid = make_uid(
        artist_name,
        d.isoformat(),
        time_str,
        musical,
        theatre,
        role,
        "|".join([f"{a}:{r}" for a, r in cast_pairs])
    )

    return {
        "uid": uid,
        "start": start,
        "end": end,
        "summary": summary,
        "location": location,
        "desc": desc,
    }


def write_ics(artist_name: str, events: list, out_path: Path):
    now_utc = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_ics(artist_name)} 演出排期",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]

    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{now_utc}",
            f"DTSTART;TZID=Asia/Shanghai:{e['start'].strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Shanghai:{e['end'].strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{escape_ics(e['summary'])}",
            f"LOCATION:{escape_ics(e['location'])}",
            f"DESCRIPTION:{escape_ics(e['desc'])}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    today = dt.date.today()
    end_date = today + dt.timedelta(days=FUTURE_DAYS)
    total_days = (end_date - START_DATE).days + 1

    events_by_artist = {aid: [] for aid in ARTISTS}
    seen_uid = {aid: set() for aid in ARTISTS}

    for idx in range(total_days):
        d = START_DATE + dt.timedelta(days=idx)
        if idx % 50 == 0 or idx == total_days - 1:
            print(f"[DAY {idx+1}/{total_days}] {d.isoformat()}")

        try:
            shows = fetch_day(d.isoformat())
        except Exception as e:
            print(f"[ERR DAY] {d.isoformat()} {e}")
            continue

        for show in shows:
            for c in (show.get("cast") or []):
                artist_in_show = (c.get("artist") or "").strip()
                role_in_show = (c.get("role") or "").strip()

                for artist_id, artist_name in ARTISTS.items():
                    if artist_in_show == artist_name:
                        ev = build_event_for_artist(artist_name, role_in_show, d, show)
                        if ev["uid"] in seen_uid[artist_id]:
                            continue
                        seen_uid[artist_id].add(ev["uid"])
                        events_by_artist[artist_id].append(ev)

    for artist_id, artist_name in ARTISTS.items():
        evs = sorted(events_by_artist[artist_id], key=lambda x: x["start"])
        out_file = OUT / f"artist_{artist_id}.ics"
        write_ics(artist_name, evs, out_file)
        print(f"[OK] {artist_name}: {len(evs)} events → {out_file}")


if __name__ == "__main__":
    main()
