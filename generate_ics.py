import requests
import datetime as dt
import hashlib
from zoneinfo import ZoneInfo
from pathlib import Path

TZ = ZoneInfo("Asia/Shanghai")
BASE = "https://y.saoju.net/yyj/api/search_day/"

# 只做这两个演员：一个演员一个 ICS
ARTISTS = {
    "977": "赵奕然",
    "1779": "庞东轩",
}

# ====== 关键：决定“全部演出”的时间范围 ======
# 建议：从一个足够早的日期开始扫（比如 2018-01-01）
START_DATE = dt.date(2018, 1, 1)

# 未来还要扫多少天（比如未来 365 天）
FUTURE_DAYS = 365

# 每场演出时长（小时）
DURATION_HOURS = 2

# 请求超时
TIMEOUT = 20

OUT = Path("docs")
OUT.mkdir(exist_ok=True)

def make_uid(*parts: str) -> str:
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + "@saoju"

def fetch_day(date_str: str):
    r = requests.get(BASE, params={"date": date_str}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("show_list", [])

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

def to_dt_local(date_obj: dt.date, hhmm: str) -> dt.datetime:
    h, m = hhmm.split(":")
    return dt.datetime(date_obj.year, date_obj.month, date_obj.day, int(h), int(m), tzinfo=TZ)

def build_event(artist_name: str, role: str, d: dt.date, show: dict):
    # show 字段（根据你给的接口示例）：city / musical / theatre / time / cast
    start = to_dt_local(d, show["time"])
    end = start + dt.timedelta(hours=DURATION_HOURS)

    city = show.get("city", "")
    musical = show.get("musical", "")
    theatre = show.get("theatre", "")

    summary = f"{musical}｜{role}".strip("｜")
    location = f"{city} {theatre}".strip()

    desc_lines = [
        f"演员：{artist_name}",
        f"角色：{role}",
        f"城市：{city}",
        f"剧院：{theatre}",
    ]
    desc = "\n".join(desc_lines)

    uid = make_uid(artist_name, d.isoformat(), show.get("time", ""), musical, role, theatre)

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

    # 每个演员分别收集 events
    events_by_artist_id = {aid: [] for aid in ARTISTS.keys()}

    for idx in range(total_days):
        d = START_DATE + dt.timedelta(days=idx)
        date_str = d.isoformat()

        # 进度打印：每 50 天打印一次，方便你在 Actions 日志里看有没有在跑
        if idx % 50 == 0 or idx == total_days - 1:
            print(f"[DAY {idx+1}/{total_days}] {date_str}")

        try:
            shows = fetch_day(date_str)
        except Exception as e:
            # 某天失败不要整个任务崩掉，记录一下继续
            print(f"[ERR DAY] {date_str} {e}")
            continue

        for show in shows:
            cast_list = show.get("cast", []) or []
            # cast: [{"role": "...", "artist": "..."}]
            for cast in cast_list:
                artist = cast.get("artist", "")
                role = cast.get("role", "")

                # 只匹配我们要的两个人
                for artist_id, artist_name in ARTISTS.items():
                    if artist == artist_name:
                        ev = build_event(artist_name, role, d, show)
                        events_by_artist_id[artist_id].append(ev)

    # 去重（同一场可能重复出现时）
    for artist_id, artist_name in ARTISTS.items():
        evs = events_by_artist_id[artist_id]
        uniq = {}
        for e in evs:
            uniq[e["uid"]] = e
        evs = list(uniq.values())
        evs.sort(key=lambda x: x["start"])
        out_file = OUT / f"artist_{artist_id}.ics"
        write_ics(artist_name, evs, out_file)
        print(f"[OK] {artist_id} {artist_name}: {len(evs)} events -> {out_file}")

if __name__ == "__main__":
    main()
