# 交我创 · 开放接口

来源：`agentsquare.sjtu.edu.cn`（交我创智能体创新大赛平台）

## 鉴权

请求头带 `X-Api-Key`。Key 在页面上点「申请 Key」拿。

```http
GET https://agentsquare.sjtu.edu.cn/api/open/second-classroom
X-Api-Key: <你的 key>
```

缺 Key 或 Key 无效时返回：

```json
{"success":false,"code":500,"message":"缺少 API Key，请在请求头 X-Api-Key 中携带","data":null}
{"success":false,"code":500,"message":"API Key 无效","data":null}
```

注意：HTTP 状态码仍是 200，要看 `success` 字段。

## 获取第二课堂活动

`GET /api/open/second-classroom`

查询当天及往后 30 天内、已发布且公开的第二课堂活动。无入参。

返回数组，按活动开始时间升序。

| 字段 | 说明 |
|---|---|
| `activityTypeName` | 活动所属类型名称 |
| `name` | 活动名称 |
| `status` | 活动状态 |
| `introduction` | 活动简介 |
| `presenter` | 主讲人 |
| `presenterBio` | 主讲人简介 |
| `address` | 活动地点 |
| `startTime` | 活动开始时间 |
| `endTime` | 活动结束时间 |
| `sponsor` | 主办方 |
| `displayUrl` | 活动图片展示链接 |
| `signupMethod` | 活动报名方式 |
| `signupStartTime` | 报名开始时间 |
| `signupEndTime` | 报名结束时间 |
| `personNum` | 招募人数 |

## 信鸽里怎么配

Key 存在 `~/.xinge/config.json` 的 `sjtu_api_key`，或者环境变量 `SJTU_API_KEY`。

```bash
# 临时试一下
export SJTU_API_KEY=你的key
```

界面里在「机器人」或「订阅」里填都行。

实现见 `xinge/sources/second_classroom.py`。
