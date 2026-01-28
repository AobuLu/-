import requests
import datetime as dt
import hashlib
from zoneinfo import ZoneInfo
from pathlib import Path

TZ = ZoneInfo("Asia/Shanghai")
BASE = "https://y.saoju.net/yyj/api/search_day/"

# 在这里写你要做日历的演员
ARTISTS = {
    "977": "赵奕然"
}

DAYS = 180          # 往后生成多少天
DURATION_HOURS = 2  # 每场演出 2 小时

OUT = Path("docs")
OUT.mkdir(exist_ok=True)

def make_uid(*parts):
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + "@saoju"

def fetch_day(date):
    r = requests.get(BASE, params={"date": date}, timeout=15)
    r.raise_for_status()
    return r.json().get("show_list", [])

def escape(s):
    if not s:
        return ""
    s = str(s)
    return (
        s.replace("\\", "\\\\")
         .replace(";", "\\;")
         .replace(",", "\\,")
         .replace("\n", "\\n")
    )

today = dt.date.today()

for artist_id, artist_name in ARTISTS.items():
    events = []

    for i in range(DAYS):
        d = today + dt.timedelta(days=i)
        shows = fetch_day(d.isoformat())

        for s in shows:
            for c in s.get("cast", []):
                if c.get("artist") == artist_name:
                    h, m = s["time"].split(":")
                    start = dt.datetime(
                        d.year, d.month, d.day, int(h), int(m), tzinfo=TZ
                    )
                    end = start + dt.timedelta(hours=DURATION_HOURS)

                    desc = "\n".join([
                        f"演员：{artist_name}",
                        f"角色：{c.get('role','')}",
                        f"城市：{s.get('city','')}",
                        f"剧院：{s.get('theatre','')}"
                    ])

                    events.append({
                        "uid": make_uid(artist_name, d.isoformat(), s["time"], s["musical"]),
                        "start": start,
                        "end": end,
                        "summary": f"{s['musical']}｜{c.get('role','')}",
                        "location": f"{s.get('city','')} {s.get('theatre','')}",
                        "desc": desc
                    })

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{artist_name} 演出排期",
        "X-WR-TIMEZONE:Asia/Shanghai"
    ]

    now = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    for e in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{now}",
            f"DTSTART;TZID=Asia/Shanghai:{e['start'].strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Shanghai:{e['end'].strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{escape(e['summary'])}",
            f"LOCATION:{escape(e['location'])}",
            f"DESCRIPTION:{escape(e['desc'])}",
            "END:VEVENT"
        ]

    lines.append("END:VCALENDAR")

    (OUT / f"artist_{artist_id}.ics").write_text(
        "\n".join(lines),
        encoding="utf-8"
    )
