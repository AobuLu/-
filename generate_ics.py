import requests
import datetime as dt
import hashlib
import re
from zoneinfo import ZoneInfo
from pathlib import Path
from bs4 import BeautifulSoup

TZ = ZoneInfo("Asia/Shanghai")

# 过往/全部演出分页（包含历史 + 未来，按站点展示为准）
SHOW_PAGES_BASE = "https://y.saoju.net/yyj/artist/{artist_id}/show"

# 在这里写你要做日历的演员（id -> 名字）
ARTISTS = {
    "977": "赵奕然",
    # "1779": "庞东轩",
}

DURATION_HOURS = 2  # 每场演出 2 小时
OUT = Path("docs")
OUT.mkdir(exist_ok=True)

# 解析类似：2026年1月25日 星期日 14:00
DT_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日.*?(\d{1,2}:\d{2})")

def make_uid(*parts):
    raw = "|".join(parts).encode("utf-8")
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

def fetch_show_page(artist_id: str, page: int) -> str:
    if page == 1:
        url = SHOW_PAGES_BASE.format(artist_id=artist_id)
    else:
        url = SHOW_PAGES_BASE.format(artist_id=artist_id) + f"?page={page}"

    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.text

def parse_show_html(html: str):
    """
    从 show 页面里解析出条目：
    - date (YYYY-MM-DD)
    - time (HH:MM)
    - musical
    - role
    - city
    - theatre

    由于页面结构可能变，我们用“纯文本序列 + 正则定位日期时间”的方式，鲁棒性更高。
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

        # 经验上：日期时间后面依次出现：音乐剧名、角色、城市、剧院
        # 但有时页面会插入别的词（比如“显示同场演员”），所以我们用“向后找最可能的4个字段”策略。
        j = i + 1

        def next_nonempty(idx):
            while idx < len(tokens) and not tokens[idx]:
                idx += 1
            return idx

        j = next_nonempty(j)
        musical = tokens[j] if j < len(tokens) else ""
        j += 1

        j = next_nonempty(j)
        role = tokens[j] if j < len(tokens) else ""
        j += 1

        j = next_nonempty(j)
        city = tokens[j] if j < len(tokens) else ""
        j += 1

        j = next_nonempty(j)
        theatre = tokens[j] if j < len(tokens) else ""

        # 做一个轻量校验：musical 一般不是“时间/音乐剧/角色/剧院”等表头
        # 如果明显解析错了，就跳过
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
    翻页抓取 show 列表，直到某一页解析不到任何条目为止。
    """
    all_items = []
    seen = set()

    for page in range(1, 300):  # 上限防死循环（一般远小于300）
        html = fetch_show_page(artist_id, page)
        items = parse_show_html(html)

        if not items:
            break

        for it in items:
            key = (it["date"], it["time"], it["musical"], it["role"], it["city"], it["theatre"])
            if key not in seen:
                seen.add(key)
                all_items.append(it)

    # 按时间排序（旧 -> 新）
    def sort_key(x):
        return (x["date"], x["time"], x["musical"], x["role"])

    all_items.sort(key=sort_key)
    return all_items

# ---- 生成 ICS ----
now_stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

for artist_id, artist_name in ARTISTS.items():
    shows = fetch_all_shows_for_artist(artist_id)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{artist_name} 演出排期",
        "X-WR-TIMEZONE:Asia/Shanghai"
    ]

    for s in shows:
        d = dt.date.fromisoformat(s["date"])
        h, m = s["time"].split(":")
        start = dt.datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=TZ)
        end = start + dt.timedelta(hours=DURATION_HOURS)

        desc = "\n".join([
            f"演员：{artist_name}",
            f"角色：{s.get('role','')}",
            f"城市：{s.get('city','')}",
            f"剧院：{s.get('theatre','')}",
        ])

        uid = make_uid(artist_id, artist_name, s["date"], s["time"], s["musical"], s.get("role",""))

        summary = f"{s['musical']}｜{s.get('role','')}".strip("｜")

        location = f"{s.get('city','')} {s.get('theatre','')}".strip()

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;TZID=Asia/Shanghai:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Shanghai:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{escape(summary)}",
            f"LOCATION:{escape(location)}",
            f"DESCRIPTION:{escape(desc)}",
            "END:VEVENT"
        ]

    lines.append("END:VCALENDAR")

    (OUT / f"artist_{artist_id}.ics").write_text("\n".join(lines), encoding="utf-8")
