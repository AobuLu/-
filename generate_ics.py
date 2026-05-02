import requests
import datetime as dt
import hashlib
import re
from zoneinfo import ZoneInfo
from pathlib import Path

TZ = ZoneInfo("Asia/Shanghai")
BASE = "https://y.saoju.net/yyj/api/search_day/"

ARTISTS = {
    "977": "赵奕然",
    "1779": "庞东轩",
}

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
        "同场演员：",
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

    parts = [musical]
    if label:
        parts.append(label)
    parts.append(artist)
    if role:
        parts.append(role)
    summary = "｜".join(parts)

    cast_pairs = normalize_cast(show)

    uid = make_uid(
        artist,
        d.isoformat(),
        time_str,
        musical,
        theatre,
        role,
    )

    return {
        "uid": uid,
        "start": start,
        "end": end,
        "summary": summary,
        "location": f"{city} {theatre}".strip(),
        "desc": build_desc(artist, role, city, theatre, cast_pairs),
    }


def ics_dtstamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def extract_uid(block: str) -> str:
    m = re.search(r"^UID:(.+)$", block, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def extract_start_for_sort(block: str) -> dt.datetime:
    m = re.search(
        r"^DTSTART(?:;TZID=Asia/Shanghai)?:([0-9]{8}T[0-9]{6})$",
        block,
        flags=re.MULTILINE,
    )
    if not m:
        return dt.datetime.min.replace(tzinfo=TZ)

    raw = m.group(1)
    try:
        parsed = dt.datetime.strptime(raw, "%Y%m%dT%H%M%S")
        return parsed.replace(tzinfo=TZ)
    except Exception:
        return dt.datetime.min.replace(tzinfo=TZ)


def refresh_event_block(block: str, now_stamp: str) -> str:
    """
    保留旧事件内容，但刷新 DTSTAMP / LAST-MODIFIED。
    这样 iPhone 更容易识别到订阅源已经更新。
    """
    block = block.strip()

    if re.search(r"^DTSTAMP:", block, flags=re.MULTILINE):
        block = re.sub(
            r"^DTSTAMP:.*$",
            f"DTSTAMP:{now_stamp}",
            block,
            flags=re.MULTILINE,
        )
    else:
        block = block.replace("BEGIN:VEVENT", f"BEGIN:VEVENT\nDTSTAMP:{now_stamp}", 1)

    if re.search(r"^LAST-MODIFIED:", block, flags=re.MULTILINE):
        block = re.sub(
            r"^LAST-MODIFIED:.*$",
            f"LAST-MODIFIED:{now_stamp}",
            block,
            flags=re.MULTILINE,
        )
    else:
        block = block.replace(f"DTSTAMP:{now_stamp}", f"DTSTAMP:{now_stamp}\nLAST-MODIFIED:{now_stamp}", 1)

    return block


def parse_existing_ics(path: Path, now_stamp: str):
    """
    读取旧 ICS，保留历史事件；
    但不再原封不动写回，而是刷新 DTSTAMP / LAST-MODIFIED。
    """
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8")
    blocks = text.split("BEGIN:VEVENT")
    if len(blocks) == 1:
        return {}

    events = {}

    for block in blocks[1:]:
        event_text = "BEGIN:VEVENT" + block
        if "END:VEVENT" not in event_text:
            continue

        event_text = event_text.split("END:VEVENT", 1)[0] + "END:VEVENT"

        uid = extract_uid(event_text)
        if not uid:
            continue

        events[uid] = refresh_event_block(event_text, now_stamp)

    return events


def build_event_block(ev: dict, now_stamp: str) -> str:
    return "\n".join([
        "BEGIN:VEVENT",
        f"UID:{ev['uid']}",
        f"DTSTAMP:{now_stamp}",
        f"LAST-MODIFIED:{now_stamp}",
        f"DTSTART;TZID=Asia/Shanghai:{ev['start'].strftime('%Y%m%dT%H%M%S')}",
        f"DTEND;TZID=Asia/Shanghai:{ev['end'].strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:{escape_ics(ev['summary'])}",
        f"LOCATION:{escape_ics(ev['location'])}",
        f"DESCRIPTION:{escape_ics(ev['desc'])}",
        "END:VEVENT",
    ])


def write_ics(path: Path, event_blocks: list):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "PRODID:-//Saoju Artist Calendar//CN//",
        "X-WR-CALNAME:演出排期",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "X-PUBLISHED-TTL:PT30M",
        "REFRESH-INTERVAL;VALUE=DURATION:PT30M",
    ]

    lines.extend(event_blocks)
    lines.append("END:VCALENDAR")

    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    today = dt.date.today()
    start_date = today - dt.timedelta(days=LOOKBACK_DAYS)
    end_date = today + dt.timedelta(days=FUTURE_DAYS)

    total_days = (end_date - start_date).days + 1
    now_stamp = ics_dtstamp()

    for artist_id, artist_name in ARTISTS.items():
        path = OUT / f"artist_{artist_id}.ics"

        event_map = parse_existing_ics(path, now_stamp)

        print(f"开始更新：{artist_name}")

        added_or_updated = 0

        for i in range(total_days):
            d = start_date + dt.timedelta(days=i)

            if i % 50 == 0 or i == total_days - 1:
                print(f"{artist_name} [{i + 1}/{total_days}] {d.isoformat()}")

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

                        # 用最新 API 结果覆盖同 UID 的旧事件
                        event_map[ev["uid"]] = build_event_block(ev, now_stamp)
                        added_or_updated += 1

        all_blocks = list(event_map.values())

        # 关键：按演出时间从新到旧排序，让未来/最新排期排在文件前面
        all_blocks.sort(key=extract_start_for_sort, reverse=True)

        write_ics(path, all_blocks)

        print(f"{artist_name} 新增或更新 {added_or_updated} 场")
        print(f"[OK] {artist_name} -> {path}")


if __name__ == "__main__":
    main()
