"""主动告警：jAccount 登录过期 + 信息源连续抓取失败。

背景：cookie 失效后所有抓取都会静默失败，用户完全无感；某个源挂了也只在
「诊断」里看得到。这两类问题都该主动推一条提醒，但**不能刷屏**。

分工：
  * 本模块只管检测和去重，提醒统一写进 reminder 表；
  * bot 的 due 接口不按 target_type 过滤，所以写进去就能被 QQ 插件推出去；
  * 不改 db.py / reminders.py（别的分支在改），去重状态用自己的
    alert_state 表（CREATE TABLE IF NOT EXISTS，上线零冲突）。

规则：
  * jAccount 过期
      - ~/.xinge/session.json 里没有 JAAuthCookie → 判定过期（不用联网）；
      - cookie 文件在也不代表服务端还认，再用 Canvas 的 /api/v1/users/self
        探一次：401/登录页 → 过期，200 拿到用户信息 → 正常；
      - 探不通（断网等）时看这一轮是不是有 ≥2 个要登录的源同时报登录类
        错误，是也算过期（宁可提醒，也别静默失败）。
      - 判定过期 → 生成一条提醒，同一天最多一条；恢复登录后清掉标记，
        下次再失效还能提醒。
  * 抓取失败
      - 每轮抓取后看 sources_state：某个源 last_ok=0 连续 3 次 → 生成提醒，
        body 带 last_error 前 100 字；
      - 「一次」= 一次告警检测（调度器每轮抓取后正好调一次），所以
        引擎里看到 last_ok=0 就 +1，连续 3 轮失败触发；
      - 每个源每天最多一条提醒；该源恢复成功后清掉计数和当天标记；
      - 「用户根本没配」（error 里带「还没有配置 / 未配置 / 没配置 / 没有配置」）
        不算抓取故障：不计数、不告警，看到就当正常轮次处理。
  * 提醒文案要能直接指导行动：body 末尾带一句「下一步点哪里」
    （jAccount 怎么重新扫码、cnmooc 从 App 哪个入口重新登录）。

去重状态在 alert_state 表里，一行一个 key（auth / fetch:<源名>）：
    fails        连续失败次数
    last_run     上次看到的 sources_state.last_run（排查用；失败按「每轮检测」计数）
    last_day     最近一次告警的日期（YYYY-MM-DD，按天去重）
    reminder_id  最近一次告警对应的 reminder.id —— 恢复/换天重新告警时把旧
                 的、还没推出去的 pending 顺手删掉，避免积压几条一样的
    updated_at   状态最后写入时间
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

from . import db

log = logging.getLogger("xinge.alerts")

# 连续失败多少次才告警
FAIL_THRESHOLD = 3
# 抓取失败提醒的 body 最多带多少字 last_error
ERROR_BODY_LIMIT = 100

# jAccount 过期提醒的文案：第一行说问题，第二行直接说去哪点
AUTH_TITLE = "jAccount 登录可能过期"
AUTH_BODY = (
    "扫码重新登录一下，否则抓不到新通知\n"
    "打开信鸽 App 点「退出登录」再扫码，或直接点登录"
)

# 这些错误表示「用户根本没配 / 没订阅」，不是抓取故障：不计数、不告警
CONFIG_MISSING_HINTS = ("还没有配置", "未配置", "没配置", "没有配置")

# 各源失败时附带的「下一步点哪里」提示（没写的源就只带错误信息）
SOURCE_ACTION_HINTS = {
    "cnmooc": "打开 App → 订阅 → 好大学在线 → 重新登录",
}

# 用哪个接口验 jAccount 还活着（Canvas 和 jAccount 共用一套会话）
CANVAS_PROBE = "https://oc.sjtu.edu.cn/api/v1/users/self"

# 依赖 jAccount 的源：探不通时拿它们的错误做旁证
LOGIN_SOURCES = frozenset(
    {"canvas", "cnmooc", "pe", "second_classroom", "aihaoke", "shuiyuan", "activity"}
)
# 看起来像「登录态失效」的错误关键词（全部按小写匹配）
LOGIN_ERROR_HINTS = (
    "登录",
    "登陆",
    "扫码",
    "sso",
    "jaccount",
    "unauthorized",
    "401",
    "会话失效",
    "会话已失效",
    "会话过期",
    "not logged",
)

# 信息源的展示名（和 routers/sources.py 的 KNOWN 保持一致，避免跨模块循环导入）
SOURCE_NAMES = {
    "canvas": "Canvas 课程平台",
    "cnmooc": "好大学在线 · 站内消息",
    "pe": "物理实验通知",
    "second_classroom": "第二课堂活动",
    "rss": "RSS 订阅",
    "aihaoke": "知途好课",
    "college": "学院公告",
    "shuiyuan": "水源社区",
}

# 自己的去重表。放这里而不是 db.py 的 SCHEMA 里，零冲突
ALERT_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_state (
    key         TEXT PRIMARY KEY,
    fails       INTEGER NOT NULL DEFAULT 0,
    last_run    TEXT DEFAULT '',
    last_day    TEXT DEFAULT '',
    reminder_id INTEGER,
    updated_at  TEXT NOT NULL
);
"""


# ---------------- 小工具 ----------------

def _now() -> datetime:
    return datetime.now()


def _today() -> date:
    """今天（单独抽出来，测试可以替换）。"""
    return date.today()


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _session_path() -> Path:
    """jAccount 会话文件。跟 SessionStore 用同一个目录（支持 XINGE_HOME）。"""
    try:
        from xinge.auth.session import DEFAULT_HOME

        return DEFAULT_HOME / "session.json"
    except Exception:                             # noqa: BLE001
        home = os.environ.get("XINGE_HOME") or (Path.home() / ".xinge")
        return Path(home) / "session.json"


def _source_name(key: str) -> str:
    return SOURCE_NAMES.get(key) or key


def _ensure_schema(conn) -> None:
    conn.execute(ALERT_SCHEMA)


def _load_state(conn, key: str) -> dict:
    row = conn.execute("SELECT * FROM alert_state WHERE key=?", (key,)).fetchone()
    return dict(row) if row else {}


def _save_state(
    conn,
    key: str,
    *,
    fails: int = 0,
    last_run: str = "",
    last_day: str = "",
    reminder_id=None,
) -> None:
    conn.execute(
        """INSERT INTO alert_state(key, fails, last_run, last_day, reminder_id, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(key) DO UPDATE SET
             fails=excluded.fails,
             last_run=excluded.last_run,
             last_day=excluded.last_day,
             reminder_id=excluded.reminder_id,
             updated_at=excluded.updated_at""",
        (key, int(fails), last_run or "", last_day or "", reminder_id, _fmt(_now())),
    )


def _insert_reminder(conn, title: str, body: str) -> int:
    """往 reminder 队列塞一条「现在就该推」的提醒，返回 id。

    直接写 SQL：reminders.py 在别的分支上改着，这里只依赖已经定下来的表结构。
    """
    stamp = _fmt(_now())
    cur = conn.execute(
        """INSERT INTO reminder(title, body, fire_at, target_type, target_id, state, created_at)
           VALUES (?,?,?,'alert',NULL,'pending',?)""",
        (title.strip() or "提醒", (body or "").strip(), stamp, stamp),
    )
    return int(cur.lastrowid or 0)


def _drop_pending(conn, reminder_id) -> None:
    """删掉自己生成、还没推出去的那条提醒（sent / skipped 的留着，别动历史）。"""
    if not reminder_id:
        return
    conn.execute("DELETE FROM reminder WHERE id=? AND state='pending'", (int(reminder_id),))


# ---------------- jAccount 登录态 ----------------

def _read_jaccount_cookies() -> tuple[bool, str]:
    """本地判断：会话文件里还有没有 JAAuthCookie。返回 (有没有, 说明)。"""
    path = _session_path()
    if not path.exists():
        return False, f"没有 {path.name}（还没登录过，或登录态被清了）"
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"{path.name} 读不出来：{exc}"
    if not isinstance(records, list):
        return False, f"{path.name} 格式不对"
    for record in records:
        if not isinstance(record, dict) or record.get("name") != "JAAuthCookie":
            continue
        if not str(record.get("value") or "").strip():
            continue
        domain = str(record.get("domain") or "").lower()
        if domain and "jaccount" not in domain:
            continue
        return True, ""
    return False, f"{path.name} 里没有 JAAuthCookie"


def _probe_canvas() -> tuple[str, str]:
    """拿本机会话调一次 Canvas 接口，判断 jAccount 还认不认。返回 (状态, 说明)。

    状态：
      ok       —— 能读到用户信息
      expired  —— 401 / 被踢回登录页 / 返回的是登录页 HTML
      unknown  —— 网络不通或服务端异常，判断不了
    """
    try:
        from xinge.auth import SessionStore

        with SessionStore().client(timeout=15) as client:
            resp = client.get(CANVAS_PROBE)
    except Exception as exc:                      # noqa: BLE001
        return "unknown", f"探测 Canvas 失败：{exc}"

    status = resp.status_code
    final = str(resp.url).lower()
    if status in (401, 403):
        return "expired", f"Canvas 接口返回 {status}"
    if "/login" in final or "jaccount" in final:
        return "expired", f"Canvas 把请求踢回了登录页：{resp.url}"
    if status != 200:
        return "unknown", f"Canvas 接口返回 {status}"
    try:
        payload = resp.json()
    except ValueError:
        return "expired", "Canvas 返回的不是接口数据（多半是登录页）"
    if isinstance(payload, dict) and payload.get("id") is not None:
        return "ok", ""
    return "expired", "Canvas 没认出这个会话"


def _looks_like_login_error(error) -> bool:
    text = str(error or "").lower()
    return any(hint in text for hint in LOGIN_ERROR_HINTS)


def _is_config_missing(error) -> bool:
    """这个错误是不是「用户没配置」而不是抓取失败（RSS 没加订阅这种）。"""
    text = str(error or "")
    return any(hint in text for hint in CONFIG_MISSING_HINTS)


def _source_body(key: str, error, limit: int = ERROR_BODY_LIMIT) -> str:
    """源失败提醒的正文：last_error 前 N 字 + 能直接照做的一步提示。"""
    body = str(error or "").strip()[:limit]
    if not body:
        body = "（源没有给出错误信息）"
    hint = SOURCE_ACTION_HINTS.get(key)
    if hint:
        body += f"\n{hint}"
    return body


def _login_error_sources(round_results=None, states=None) -> list[str]:
    """这一轮里同时报「登录失效」类错误的源。

    round_results 是 collector.fetch_all() 的返回值；没传就看 sources_state。
    """
    if round_results is not None:
        pairs = [
            (r.get("key"), r.get("ok"), r.get("error")) for r in round_results
        ]
    else:
        pairs = [
            (s.get("key"), s.get("last_ok"), s.get("last_error"))
            for s in (states or [])
        ]
    hits: list[str] = []
    for key, ok, error in pairs:
        if key in LOGIN_SOURCES and not ok and _looks_like_login_error(error):
            hits.append(key)
    return hits


def detect_auth(round_results=None, states=None) -> dict:
    """判断 jAccount 登录态。返回 {"state": ok/expired/unknown, "via": ..., "detail": ...}。

    顺序：本地会话文件 → Canvas 实测 → 多源同时报登录错误的旁证。
    探测不确定（断网）时返回 unknown，此时**不动**告警标记，避免误报。
    """
    ok, why = _read_jaccount_cookies()
    if not ok:
        return {"state": "expired", "via": "session", "detail": why}

    state, detail = _probe_canvas()
    if state in ("ok", "expired"):
        return {"state": state, "via": "canvas", "detail": detail}

    hits = _login_error_sources(round_results, states)
    if len(hits) >= 2:
        return {
            "state": "expired",
            "via": "sources",
            "detail": f"{len(hits)} 个源同时报登录失效：{'、'.join(hits)}",
        }
    return {"state": "unknown", "via": "canvas", "detail": detail}


# ---------------- 规则：jAccount 过期 ----------------

AUTH_KEY = "auth"


def _check_auth(conn, auth: dict, today: str) -> list[dict]:
    state = _load_state(conn, AUTH_KEY)
    last_day = state.get("last_day") or ""

    if auth["state"] == "expired":
        if last_day == today:
            return []                             # 今天已经提醒过，不刷屏
        _drop_pending(conn, state.get("reminder_id"))
        reminder_id = _insert_reminder(conn, AUTH_TITLE, AUTH_BODY)
        _save_state(conn, AUTH_KEY, last_day=today, reminder_id=reminder_id)
        log.warning("jAccount 登录可能过期（%s），已生成提醒 #%s",
                    auth.get("detail"), reminder_id)
        return [
            {
                "kind": "auth",
                "title": AUTH_TITLE,
                "body": AUTH_BODY,
                "reminder_id": reminder_id,
                "detail": auth.get("detail", ""),
            }
        ]

    if auth["state"] == "ok" and (last_day or state.get("reminder_id")):
        # 恢复了：清标记 + 撤掉还没推出去的过期提醒，下次再失效能重新提醒
        _drop_pending(conn, state.get("reminder_id"))
        _save_state(conn, AUTH_KEY, last_day="", reminder_id=None)
        log.info("jAccount 登录恢复正常，告警标记已清")
    return []


# ---------------- 规则：源连续抓取失败 ----------------

def _check_sources(conn, states: list[dict], today: str) -> list[dict]:
    created: list[dict] = []
    for st in states:
        key = st.get("key") or ""
        if not key:
            continue
        state_key = f"fetch:{key}"
        prev = _load_state(conn, state_key)
        fails = int(prev.get("fails") or 0)
        last_run = st.get("last_run") or ""
        last_day = prev.get("last_day") or ""
        config_missing = _is_config_missing(st.get("last_error"))

        # 抓成功，或者只是「用户没配置」：都不算故障。
        # 计数清零、当天标记清空，顺带撤掉还没推出去的旧告警
        # （比如用户把 RSS 订阅删了，就不该再收到「RSS 抓取失败」）。
        if st.get("last_ok") or config_missing:
            if fails or last_day or prev.get("reminder_id"):
                _drop_pending(conn, prev.get("reminder_id"))
                _save_state(conn, state_key, fails=0, last_run=last_run,
                            last_day="", reminder_id=None)
                if st.get("last_ok"):
                    log.info("%s 抓取恢复正常，失败计数和告警标记已清", _source_name(key))
                else:
                    log.info("%s 未配置，不计入失败，告警标记已清", _source_name(key))
            elif not prev:
                _save_state(conn, state_key, fails=0, last_run=last_run)
            continue

        # 抓失败：调度器每轮抓取后调一次 check，一次 check 算一轮，
        # 看到 last_ok=0 就计数 +1；连续 FAIL_THRESHOLD 轮失败就告警。
        fails += 1

        if fails >= FAIL_THRESHOLD and last_day != today:
            title = f"{_source_name(key)} 连续 {FAIL_THRESHOLD} 次抓取失败"
            body = _source_body(key, st.get("last_error"))
            _drop_pending(conn, prev.get("reminder_id"))
            reminder_id = _insert_reminder(conn, title, body)
            _save_state(conn, state_key, fails=fails, last_run=last_run,
                        last_day=today, reminder_id=reminder_id)
            log.warning("%s（已连续 %s 次失败），已生成提醒 #%s：%s",
                        title, fails, reminder_id, body)
            created.append(
                {
                    "kind": "source",
                    "source": key,
                    "title": title,
                    "body": body,
                    "reminder_id": reminder_id,
                    "fails": fails,
                }
            )
            continue

        _save_state(conn, state_key, fails=fails, last_run=last_run,
                    last_day=last_day, reminder_id=prev.get("reminder_id"))
    return created


# ---------------- 对外入口 ----------------

def check(round_results: list[dict] | None = None) -> dict:
    """跑一轮告警检测：jAccount 登录态 + 信息源连续失败。

    Args:
        round_results: 这一轮 collector.fetch_all() 的返回值；定时任务传，
            手动调用可以不传（那就用 sources_state 里的错误做旁证）。

    Returns:
        {"ok": True, "created": 新生成的提醒数, "items": [...], "auth": {...}, "checked_at": ...}
    """
    today = _today().isoformat()
    try:
        states = db.source_states()
    except Exception as exc:                      # noqa: BLE001
        log.warning("读 sources_state 失败：%s", exc)
        states = []

    try:
        auth = detect_auth(round_results, states)
    except Exception as exc:                      # noqa: BLE001
        log.warning("判断 jAccount 登录态出错：%s", exc)
        auth = {"state": "unknown", "via": "error", "detail": str(exc)}

    created: list[dict] = []
    conn = db.connect()
    try:
        _ensure_schema(conn)
        created.extend(_check_auth(conn, auth, today))
        created.extend(_check_sources(conn, states, today))
        conn.commit()
    finally:
        conn.close()

    report = {
        "ok": True,
        "created": len(created),
        "items": created,
        "auth": auth,
        "checked_at": _fmt(_now()),
    }
    if created:
        log.info("告警检测：新生成 %s 条提醒", len(created))
    return report


def status() -> dict:
    """当前去重状态（给诊断 / 手动排查看）。"""
    conn = db.connect()
    try:
        _ensure_schema(conn)
        rows = [dict(r) for r in conn.execute("SELECT * FROM alert_state ORDER BY key")]
    finally:
        conn.close()
    return {
        "fail_threshold": FAIL_THRESHOLD,
        "error_body_limit": ERROR_BODY_LIMIT,
        "per_day_limit": 1,
        "session_file": str(_session_path()),
        "states": rows,
    }


def main(argv: list[str] | None = None) -> int:
    """命令行入口：手动跑一轮 / 查看状态。

        python -m app.server.alerts --check     # 跑一轮检测（会写库，可能生成提醒）
        python -m app.server.alerts --status    # 看 alert_state 去重状态
        python -m app.server.alerts --auth      # 只看 jAccount 判定，不写库
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.server.alerts", description="信鸽告警检测"
    )
    parser.add_argument("--check", action="store_true", help="跑一轮检测（写库）")
    parser.add_argument("--status", action="store_true", help="打印 alert_state 表")
    parser.add_argument("--auth", action="store_true", help="只打印 jAccount 判定，不写库")
    args = parser.parse_args(argv)

    if not (args.check or args.status or args.auth):
        parser.print_help()
        return 2
    if args.auth:
        print(json.dumps(detect_auth(), ensure_ascii=False, indent=2))
    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    if args.check:
        report = check()
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if report.get("ok") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
