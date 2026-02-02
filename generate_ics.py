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

# ====== “全部演出”覆盖范围 ======
# 历史从什么时候开始扫：越早越全，但跑得越久
START_DATE = dt.date(2018, 1, 1)

# 未来扫多少天
FUTURE_DAYS = 365

# 每场演出时长（小时）
DURATION_HOURS = 2

# 请求超时
TIMEOUT = 20

# 输出目录（GitHub Pages 你配置的是 docs/）
OUT = Path("docs")
OUT.mkdir(exist_ok=True)


def make_uid(*parts: str) -> str:
    raw = "|".join([str(p) for p in parts]).encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + "@saoju"


def escape_ics(s: str) -> str:
    """
    iCalendar 文本转义：逗号/分号/反斜杠/换行都需要处理
    """
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
    """
    GET /api/search_day/?date=YYYY-MM-DD
    返回 list[show]
    """
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


def normalize_cast(show: dict):
    """
    把 show["cast"] 规范化成 list[(artist, role)]
    """
    out = []
    for c in (show.get("cast") or []):
        a = (c.get("artist") or "").strip()
        r = (c.get("role") or "").strip()
        if a:
            out.append((a, r))
    return out


def build_description(main_artist: str, main_role: str, city: str, theatre: str, cast_pairs: list):
    """
    DESCRIPTION 中显示同场演员（完整卡司）
    把当前日历主角标注出来，便于阅读
    """
    lines = []
    lines.append(f"演员：{main_artist}")
    if main_role:
        lines.append(f"角色：{main_role}")

    lines.append("")  # 空行

    # 同场演员：把主角放最前，然后列出其他人
    lines.append("同场演员（本场完整卡司）：")

    # 主角行
    main_line = f"- {main_artist}"
    if main_role:
        main_line += f"（{main_role}）"
    main_line += "  ← 本日历主角"
    lines.append(main_line)

    # 其他演员
    for a, r in cast_pairs:
        if a == main_artist:
            continue
        if r:
            lines.append(f"- {a}（{r}）")
        else:
            lines.append(f"- {a}")

    lines.append("")  # 空行

    if city:
        lines.append(f"城市：{city}")
    if theatre:
        lines.append(f"剧院：{theatre}")

    return "\n".join(lines)


def build_event_for_artist(artist_name: str, role: str, d: dt.date, show: dict):
    """
    把当天某场 show 构造成 VEVENT（2小时）
    """
    start = to_dt_local(d, show["time"])
    end = start + dt.timedelta(hours=DURATION_HOURS)

    city = (show.get("city") or "").strip()
    musical = (show.get("musical") or "").strip()
    theatre = (show.get("theatre") or "").strip()

    cast_pairs = normalize_cast(show)

    # 标题尽量短：剧名｜角色
    summary = f"{musical}｜{role}".strip("｜")

    # Location：城市 + 剧院
    location = f"{city} {theatre}".strip()

    desc = build_description(
        main_artist=artist_name,
        main_role=role,
        city=city,
        theatre=theatre,
        cast_pairs=cast_pairs
    )

    # UID：尽量保证同一场唯一且稳定
    uid = make_uid(
        artist_name,
        d.isoformat(),
        show.get("time", ""),
        musical,
        theatre,
        role,
        # 把卡司也纳入 UID（防止同一时间同一剧院剧名但卡司不同的奇葩情况）
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
    """
    写出标准 ICS
    """
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

    # 用 uid 去重（同一场可能在接口里重复返回）
    seen_uid = {aid: set() for aid in ARTISTS.keys()}

    for idx in range(total_days):
        d = START_DATE + dt.timedelta(days=idx)
        date_str = d.isoformat()

        # 日志：每 50 天打印一次，避免 Actions 日志爆炸
        if idx % 50 == 0 or idx == total_days - 1:
            print(f"[DAY {idx+1}/{total_days}] {date_str}")

        try:
            shows = fetch_day(date_str)
        except Exception as e:
            # 某天失败不影响整体
            print(f"[ERR DAY] {date_str} {e}")
            continue

        if not shows:
            continue

        # 遍历每一场演出
        for show in shows:
            cast_list = show.get("cast", []) or []
            if not cast_list:
                continue

            # 在这一场里，看看我们关心的两个人有没有出现
            # cast: [{"role": "...", "artist": "..."}]
            for cast in cast_list:
                artist_in_show = (cast.get("artist") or "").strip()
                role_in_show = (cast.get("role") or "").strip()

                for artist_id, artist_name in ARTISTS.items():
                    if artist_in_show == artist_name:
                        ev = build_event_for_artist(artist_name, role_in_show, d, show)
                        if ev["uid"] in seen_uid[artist_id]:
                            continue
                        seen_uid[artist_id].add(ev["uid"])
                        events_by_artist_id[artist_id].append(ev)

    # 写文件
    for artist_id, artist_name in ARTISTS.items():
        evs = events_by_artist_id[artist_id]
        evs.sort(key=lambda x: x["start"])

        out_file = OUT / f"artist_{artist_id}.ics"
        write_ics(artist_name, evs, out_file)
        print(f"[OK] {artist_id} {artist_name}: {len(evs)} events -> {out_file}")


if __name__ == "__main__":
    main()
