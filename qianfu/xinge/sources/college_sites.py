"""上海交通大学各学院网站。

来源：https://www.sjtu.edu.cn/yxsz/index.html
只保留有规模的学院，跳过继续教育、研究院、中心这类单位。

mode:
    html    —— 直接请求就能拿到列表
    browser —— 列表由 JS 渲染，要用浏览器打开再取 DOM
"""

from __future__ import annotations

COLLEGES: list[dict] = [
    # ---- 直接可抓 ----
    {"key": "pharm", "name": "药学院", "url": "https://pharm.sjtu.edu.cn/ky_kydt.html", "mode": "html"},
    {"key": "acem", "name": "安泰经济与管理学院", "url": "https://www.iir.sjtu.edu.cn/news/index.html", "mode": "html"},
    {"key": "life", "name": "生命科学技术学院", "url": "http://life.sjtu.edu.cn/Data/List/tz", "mode": "html"},
    {"key": "ma", "name": "马克思主义学院", "url": "http://ma.sjtu.edu.cn/xkky/xsdt.htm", "mode": "html"},
    {"key": "tiyuxi", "name": "体育系", "url": "http://tiyuxi.sjtu.edu.cn/xwgg1/tzgg.htm", "mode": "html"},
    {"key": "law", "name": "凯原法学院", "url": "http://law.sjtu.edu.cn/", "mode": "html"},
    {"key": "naoce", "name": "船舶海洋与建筑工程学院", "url": "https://naoce.sjtu.edu.cn/dj_dt.html", "mode": "html"},
    {"key": "senergy", "name": "智慧能源创新学院", "url": "http://www.senergy.sjtu.edu.cn/index/tongzhigonggao.html", "mode": "html"},
    # ---- 结构待适配（先按 html 试，不行再说） ----
    {"key": "scce", "name": "化学化工学院", "url": "http://scce.sjtu.edu.cn/", "mode": "html"},
    {"key": "smse", "name": "材料科学与工程学院", "url": "http://smse.sjtu.edu.cn/", "mode": "html"},
    {"key": "math", "name": "数学科学学院", "url": "https://math.sjtu.edu.cn/", "mode": "html"},
    {"key": "design", "name": "设计学院", "url": "https://designschool.sjtu.edu.cn/dynamic/notice", "mode": "html"},
    {"key": "soe", "name": "教育学院", "url": "https://soe.sjtu.edu.cn/Web/NewsThree/266", "mode": "html"},
    {"key": "bme", "name": "生物医学工程学院", "url": "http://bme.sjtu.edu.cn/Web/Notice/355", "mode": "html"},
    {"key": "shss", "name": "人文学院", "url": "http://shss.sjtu.edu.cn/Web/Home", "mode": "html"},
    {"key": "sipa", "name": "国际与公共事务学院", "url": "http://www.sipa.sjtu.edu.cn/", "mode": "todo"},
    {"key": "speit", "name": "巴黎卓越工程师学院", "url": "http://speit.sjtu.edu.cn/about/notice", "mode": "html"},
    {"key": "gift", "name": "溥渊未来技术学院", "url": "https://gift.sjtu.edu.cn/develop/announcements", "mode": "html"},
    # ---- JS 渲染，用浏览器 ----
    {"key": "me", "name": "机械与动力工程学院", "url": "https://me.sjtu.edu.cn/tzgg.html", "mode": "browser"},
    {"key": "seiee", "name": "电子信息与电气工程学院", "url": "https://www.seiee.sjtu.edu.cn/", "mode": "todo"},
    {"key": "see", "name": "电气工程学院", "url": "https://see.sjtu.edu.cn", "mode": "browser"},
    {"key": "sais", "name": "自动化与感知学院", "url": "https://sais.sjtu.edu.cn/", "mode": "todo"},
    {"key": "cs", "name": "计算机学院", "url": "https://www.cs.sjtu.edu.cn/", "mode": "todo"},
    {"key": "icisee", "name": "集成电路学院", "url": "https://icisee.sjtu.edu.cn", "mode": "todo"},
    {"key": "sese", "name": "环境科学与工程学院", "url": "http://sese.sjtu.edu.cn/", "mode": "browser"},
    {"key": "aero", "name": "航空航天学院", "url": "http://www.aero.sjtu.edu.cn/", "mode": "browser"},
    {"key": "physics", "name": "物理与天文学院", "url": "http://www.physics.sjtu.edu.cn/xwgg.html", "mode": "html"},
    {"key": "soo", "name": "海洋学院", "url": "https://soo.sjtu.edu.cn/", "mode": "todo"},
    {"key": "agri", "name": "农业与生物学院", "url": "http://www.agri.sjtu.edu.cn/", "mode": "browser"},
    {"key": "sfl", "name": "外国语学院", "url": "http://sfl.sjtu.edu.cn/", "mode": "todo"},
    {"key": "smc", "name": "媒体与传播学院", "url": "https://smc.sjtu.edu.cn/", "mode": "browser"},
    {"key": "saif", "name": "上海高级金融学院", "url": "http://www.saif.sjtu.edu.cn/", "mode": "todo"},
    {"key": "zhiyuan", "name": "致远学院", "url": "http://zhiyuan.sjtu.edu.cn/", "mode": "todo"},
    {"key": "soai", "name": "人工智能学院", "url": "https://soai.sjtu.edu.cn/", "mode": "todo"},
    {"key": "psychology", "name": "心理学院", "url": "https://psychology.sjtu.edu.cn/", "mode": "browser"},
]
