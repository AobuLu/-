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

# ===== 增量更新范围 =====
LOOKBACK_DAYS = 2
FUTURE_DAYS = 365

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
        str(s)
        .replace("\\", "\\\\")
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


def to_dt_local(d: dt.date, t: str) -> dt.datetime:
    h, m = t.split(":")
    return dt.datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=TZ)


def session_label(t: str) -> str:
    if t in ("14:00", "14:30"):
        return "午"
    if t in ("19:00", "19:30"):
        return "晚"
    return ""


def normalize_cast(show: dict):
    out = []
    for c in show.get("cast", []) or []:
        a = (c.get("artist") or "").strip()
        r = (c.get("role") or "").strip()
        if a:
            out.append((a, r))
    return out


def build_desc(main_artist: str, main_role: str, city: str, theatre: str, cast_pairs: list):
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


def build_event(artist: str, role: str, d: dt.date, show: dict):
    start = to_dt_local(d, show["time"])
    end = start + dt.timedelta(hours=DURATION_HOURS)

    musical = (show.get("musical") or "").strip()
    city = (show.get("city") or "").strip()
    theatre = (show.get("theatre") or "").strip()
    time_str = (show.get("time") or "").strip()

    label = session_label(time_str)

    # 标题：剧名｜午/晚｜演员名｜角色名
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
        "location": f"{city} {theatre}".strip(),
        "desc": build_desc(artist, role, city, theatre, cast_pairs),
    }


def parse_existing_ics(path: Path):
    """
    读取旧 ICS，提取已有 VEVENT 原文和 UID，避免重复写入。
    这样历史事件会保留，只补充最近/未来新增内容。
    """
    if not path.exists():
        return [], set()

    text = path.read_text(encoding="utf-8")
    blocks = text.split("BEGIN:VEVENT")
    if len(blocks) == 1:
        return [], set()

    events = []
    seen_uids = set()

    for block in blocks[1:]:
        event_text = "BEGIN:VEVENT" + block
        if "UID:" not in event_text:
            continue
        uid = event_text.split("UID:", 1)[1].splitlines()[0].strip()
        if uid:
            seen_uids.add(uid)
            events.append(event_text.strip())

    return events, seen_uids


def build_event_block(ev: dict) -> str:
    now = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return "\n".join([
        "BEGIN:VEVENT",
        f"UID:{ev['uid']}",
        f"DTSTAMP:{now}",
        f"DTSTART;TZID=Asia/Shanghai:{ev['start'].strftime('%Y%m%dT%H%M%S')}",
        f"DTEND;TZID=Asia/Shanghai:{ev['end'].strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:{escape_ics(ev['summary'])}",
        f"LOCATION:{escape_ics(ev['location'])}",
        f"DESCRIPTION:{escape_ics(ev['desc'])}",
        "END:VEVENT"
    ])


def write_ics(path: Path, event_blocks: list):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]

    lines.extend(event_blocks)
    lines.append("END:VCALENDAR")

    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    today = dt.date.today()
    start_date = today - dt.timedelta(days=LOOKBACK_DAYS)
    end_date = today + dt.timedelta(days=FUTURE_DAYS)

    total_days = (end_date - start_date).days + 1

    for artist_id, artist_name in ARTISTS.items():
        path = OUT / f"artist_{artist_id}.ics"

        existing_blocks, seen_uids = parse_existing_ics(path)
        new_blocks = []

        print(f"开始更新：{artist_name}")

        for i in range(total_days):
            d = start_date + dt.timedelta(days=i)

            if i % 50 == 0 or i == total_days - 1:
                print(f"{artist_name} [{i+1}/{total_days}] {d.isoformat()}")

            try:
                shows = fetch_day(d.isoformat())
            except Exception as e:
                print(f"[ERR DAY] {artist_name} {d.isoformat()} {e}")
                continue

            for show in shows:
                for c in show.get("cast", []) or []:
                    if (c.get("artist") or "").strip() == artist_name:
                        role = (c.get("role") or "").strip()
                        ev = build_event(artist_name, role, d, show)

                        if ev["uid"] in seen_uids:
                            continue

                        seen_uids.add(ev["uid"])
                        new_blocks.append(build_event_block(ev))

        print(f"{artist_name} 新增 {len(new_blocks)} 场")

        all_blocks = existing_blocks + new_blocks
        write_ics(path, all_blocks)

        print(f"[OK] {artist_name} -> {path}")


if __name__ == "__main__":
    main()
