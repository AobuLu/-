import requests
import datetime as dt
import hashlib
import re
from zoneinfo import ZoneInfo
from pathlib import Path
from bs4 import BeautifulSoup

TZ = ZoneInfo("Asia/Shanghai")
DURATION_HOURS = 2

OUT = Path("docs")
OUT.mkdir(exist_ok=True)

ARTIST_API = "https://y.saoju.net/yyj/api/artist/"
SHOW_PAGES_BASE = "https://y.saoju.net/yyj/artist/{artist_id}/show"

# 解析形如：2026年1月25日 星期日 14:00
DT_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日.*?(\d{1,2}:\d{2})")


def make_uid(*parts):
    raw = "|".join([str(p) for p in parts]).encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + "@saoju"


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


def fetch_all_artists():
    """
    从 /api/artist/ 拉全量“演员/主创”列表。
    返回 dict: { "977": "赵奕然", ... }
    """
    r = requests.get(ARTIST_API, timeout=30)
    r.raise_for_status()
    data = r.json()

    artists = {}
    for item in data:
        pk = str(item.get("pk"))
        name = (item.get("fields") or {}).get("name")
        if pk and name:
            artists[pk] = name.strip()
    return artists


def fetch_show_page_html(artist_id: str, page: int) -> str:
    url = SHOW_PAGES_BASE.format(artist_id=artist_id)
    if page > 1:
        url += f"?page={page}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def parse_show_html(html: str):
    """
    解析 show 页得到条目列表：
    {date, time, musical, role, city, theatre}
    注意：不同演员页面结构可能不一致，这个解析是“启发式”的，
    所以我们会配合日志来判断是否需要增强解析规则。
    """
    soup = BeautifulSoup(html, "html.parser")
    tokens = [t.strip() for t in soup.stripped_strings if t.strip()]

    results = []
    i = 0
    while i < len(tokens):
        m = DT_RE.search(tokens[i])
        if not m:
            i += 1
            continue

        year, mon, day, hhmm = m.group(1), m.group(2), m.group(3), m.group(4)
        date_iso = f"{int(year):04d}-{int(mon):02d}-{int(day):02d}"

        # 经验顺序：日期时间 → 音乐剧 → 角色 → 城市 → 剧院
        j = i + 1

        def next_token(idx):
            while idx < len(tokens) and not tokens[idx]:
                idx += 1
            return idx

        j = next_token(j)
        musical = tokens[j] if j < len(tokens) else ""
        j += 1

        j = next_token(j)
        role = tokens[j] if j < len(tokens) else ""
        j += 1

        j = next_token(j)
        city = tokens[j] if j < len(tokens) else ""
        j += 1

        j = next_token(j)
        theatre = tokens[j] if j < len(tokens) else ""

        bad_headers = {"时间", "音乐剧", "角色", "剧院", "过往演出", "最新演出"}
        if musical in bad_headers or role in bad_headers:
            i += 1
            continue

        results.append({
            "date": date_iso,
            "time": hhmm,
            "musical": musical,
            "role": role,
            "city": city,
            "theatre": theatre,
        })

        i = j + 1

    return results


def fetch_all_shows_for_artist(artist_id: str):
    """
    翻页抓取 /show?page=... 直到某页解析不到条目。
    同时加两道“刹车”，避免分页失效导致跑很久：
    - 如果页面内容重复，停止
    - 连续两页解析不到内容，停止
    """
    all_items = []
    seen = set()

    prev_hash = None
    empty_streak = 0

    for page in range(1, 500):
        html = fetch_show_page_html(artist_id, page)

        h = hashlib.sha1(html.encode("utf-8")).hexdigest()
        if h == prev_hash:
            break
        prev_hash = h

        items = parse_show_html(html)

        if not items:
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue

        empty_streak = 0

        for it in items:
            key = (it["date"], it["time"], it["musical"], it["role"], it["city"], it["theatre"])
            if key not in seen:
                seen.add(key)
                all_items.append(it)

    all_items.sort(key=lambda x: (x["date"], x["time"], x["musical"], x["role"]))
    return all_items


def build_ics(artist_id: str, artist_name: str, shows: list):
    now_stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{artist_name} 演出排期",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]

    for s in shows:
        d = dt.date.fromisoformat(s["date"])
        h, m = s["time"].split(":")
        start = dt.datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=TZ)
        end = start + dt.timedelta(hours=DURATION_HOURS)

        summary = f"{s['musical']}｜{s.get('role','')}".strip("｜")
        location = f"{s.get('city','')} {s.get('theatre','')}".strip()
        desc = "\n".join([
            f"演员：{artist_name}",
            f"角色：{s.get('role','')}",
            f"城市：{s.get('city','')}",
            f"剧院：{s.get('theatre','')}",
        ])

        uid = make_uid(artist_id, artist_name, s["date"], s["time"], s["musical"], s.get("role", ""))

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;TZID=Asia/Shanghai:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Shanghai:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{escape(summary)}",
            f"LOCATION:{escape(location)}",
            f"DESCRIPTION:{escape(desc)}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\n".join(lines)


def main():
    artists = fetch_all_artists()

    generated = 0
    no_shows = 0
    errors = 0
    total = len(artists)

    # 调试时可先只跑少数几个，避免又等一小时：
    # TARGET_IDS = {"977", "1779"}
    # artists = {k: v for k, v in artists.items() if k in TARGET_IDS}

    for idx, (artist_id, artist_name) in enumerate(artists.items(), start=1):
        # 打印进度（前 50 个打印更详细，避免日志爆炸）
        if idx <= 50 or idx % 200 == 0:
            print(f"[{idx}/{total}] {artist_id} {artist_name}")

        try:
            shows = fetch_all_shows_for_artist(artist_id)
        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"[ERR] {artist_id} {artist_name}: {e}")
            continue

        if not shows:
            no_shows += 1
            if no_shows <= 20:
                print(f"[NO SHOW] {artist_id} {artist_name}")
            continue

        generated += 1
        print(f"[OK] {artist_id} {artist_name}: {len(shows)} shows")

        ics_text = build_ics(artist_id, artist_name, shows)
        (OUT / f"artist_{artist_id}.ics").write_text(ics_text, encoding="utf-8")

    print(f"SUMMARY total={total} generated={generated} no_shows={no_shows} errors={errors}")


if __name__ == "__main__":
    main()
