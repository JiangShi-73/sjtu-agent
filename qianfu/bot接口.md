# 机器人接口

给 QQ 机器人 / Agent 用的只读接口。全部挂在 `http://127.0.0.1:8722/api/v1`。

## 约定

- 每个列表接口都支持 `format=text`，直接返回一段排好版的文本，机器人转发即可，不用自己拼
- 默认 `format=json`，返回结构化数据
- 时间统一 `YYYY-MM-DD HH:MM`

## 列表

| 接口 | 说明 |
|---|---|
| `GET /bot/ping` | 存活检查 |
| `GET /bot/boards` | 有哪些板块、各多少条 |
| `GET /bot/digest` | 未推送的摘要（主动推送用） |
| `POST /bot/ack` | 标记已推送 |
| `GET /bot/deadlines` | 未来 N 天要交的（日历白名单三类） |
| `GET /bot/canvas` | Canvas 待交作业 |
| `GET /bot/cnmooc` | 慕课作业与测验 |
| `GET /bot/pe` | 体育实验 |
| `GET /bot/second-classroom` | 第二课堂活动列表 |
| `GET /bot/activity/{id}` | 单个活动详情 |
| `GET /bot/activity/{id}/shot.png` | 活动详情截图 |
| `GET /bot/colleges` | 学院列表（带订阅状态和条数） |
| `GET /bot/colleges/{key}` | 某个学院的公告 |
| `POST /bot/colleges/{key}` | 开关学院订阅 `{"enabled": false}` |
| `GET /bot/mail` | 最近邮件 |
| `GET /bot/aihaoke` | 知途好课 |
| `GET /bot/zhitu` | 知途好课条目 |
| `GET /bot/search?q=` | 全库搜标题 |

## 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `limit` | 10 | 返回条数 |
| `format` | json | `json` 或 `text` |
| `days` | 7 | 截止日期看几天内 |

## 例子

```
GET /api/v1/bot/colleges?format=text

学院公告（31 个学院）
1. 化学化工学院  29 条
2. 药学院  24 条
3. 人文学院  22 条
...
```

```
GET /api/v1/bot/colleges/pharm?limit=3&format=text

药学院（3 条）
1. 上海交通大学艾华松课题组Angew：化学合成H3K14Ub揭示H3K9甲基转移酶
   2026-11-20
2. ...
```

```
GET /api/v1/bot/second-classroom?limit=2&format=text

第二课堂（2 条）
1. 医工交叉・磁共振灌注成像——卓工学术沙龙系列活动
   2026-12-07 16:00
2. 第四届交大极限飞盘
   2026-12-08 14:00
```

```
GET /api/v1/bot/search?q=奖学金&limit=3&format=text

「奖学金」找到 3 条
1. [college] 智慧能源创新学院关于开展2025-2026学年研究生优秀奖学金评选工作的通知
2. [college] 智慧能源创新学院2026年度国防科技奖学金评审通知
3. ...
```

## 典型对话流

**「第二课堂有哪些？」**

```
GET /bot/second-classroom?limit=10&format=text   → 回复列表
```

**「第三个展开看看」**

```
GET /bot/second-classroom?limit=10              → 取第 3 条的 id
GET /bot/activity/{id}                          → 详情
GET /bot/activity/{id}/shot.png                 → 截图发过去
```

**「电院有什么通知？」**

```
GET /bot/colleges/seiee?limit=5&format=text
```

**「以后别推计算机学院了」**

```
POST /bot/colleges/cs   {"enabled": false}
```

## 完整 OpenAPI

后端起来后打开 <http://127.0.0.1:8722/docs>，所有接口都有在线调试。
