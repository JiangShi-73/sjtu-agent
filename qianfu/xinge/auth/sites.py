"""SSO 站点表。

新增站点只需往 SITES 里加一条：给出 SSO 入口 URL 和"是否已登录"的判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    sso_url: str                  # 打开它即进入该站点的登录流程
    check_url: str                # 用来判定登录态
    ok_status: int = 200
    ok_body: str | None = None    # 响应里必须出现
    fail_body: tuple[str, ...] = field(default_factory=tuple)  # 出现即未登录
    note: str = ""


SITES: tuple[Site, ...] = (
    Site(
        key="canvas",
        name="Canvas 课程平台",
        sso_url="https://oc.sjtu.edu.cn/login/openid_connect",
        check_url="https://oc.sjtu.edu.cn/api/v1/users/self",
        ok_body='"id"',
        note="OAuth2；登录后可用 /api/v1 全量接口",
    ),
    Site(
        key="pe",
        name="物理实验选课系统",
        sso_url="http://111.186.40.251/pe/Jlogin.aspx",
        check_url="http://111.186.40.251/pe/student/student.aspx",
        fail_body=("jAccount", "jalogin", "Jlogin"),
        note="通知页无需登录；选课与个人课表需要",
    ),
    Site(
        key="cnmooc",
        name="好大学在线 CNMOOC",
        sso_url="https://cnmooc.sjtu.cn/oauth/jacAuth.mooc",
        check_url="https://cnmooc.sjtu.cn/portal/myCourseIndex/1.mooc",
        fail_body=("login.mooc", "请先登录"),
        note="旧版 SSO（jalogin?sid=jamooc...）",
    ),
    Site(
        key="aihaoke",
        name="知途好课",
        sso_url=(
            "https://passport.aihaoke.net/auth/login"
            "?tenantSiteUrl=https%3A%2F%2Fsjtu.aihaoke.net&locale=zh-CN"
        ),
        check_url="",
        note="登录页需手动点「统一身份」；暂无可靠的登录态判定，标记为未检测",
    ),
)

BY_KEY = {s.key: s for s in SITES}


def select(keys: list[str] | None) -> list[Site]:
    if not keys:
        return list(SITES)
    unknown = [k for k in keys if k not in BY_KEY]
    if unknown:
        raise SystemExit(f"未知站点: {', '.join(unknown)}\n可用: {', '.join(BY_KEY)}")
    return [BY_KEY[k] for k in keys]
