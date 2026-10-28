# Person B API handoff

适用版本：`2C1-F9.1`。这里记录当前 B 分支已存在的 Stage2C 接口。C 的独立 alternative/route-aware 搜索接口未集成。

## 前端调用顺序

1. 页面加载调用 `GET /api/stage2c/status`。它只读配置，不发送模型探测请求。
2. 用户发送消息时调用 `POST /api/stage2c/chat`，传当前 Draft、Preferences、revision、Pending 和 consent。B 解释、检索，必要时选择候选，并返回短期 ticket 与预览；不改浏览器已采用状态。
3. 用户为每个地点槽选定真实候选后，点击采用，调用 `POST /api/stage2c/resolve`。本步不调用 Gemini。B 验证 ticket、目录版本、候选 key、所有硬要求和组装结果。
4. 浏览器将返回的 `draft` 和 `preferences` 一起应用；只有此处才推进已采用的状态/revision。`ready_to_calculate=true` 时，前端再调用当前 Stage2B 路线计算。
5. 若 Stage2B 返回匹配的 `verification_ref`，浏览器调用 `POST /api/stage2c/verification` 更新受证据覆盖的状态。
6. 取消或新建聊天会清掉浏览器 Pending/提案。需要清除 B 进程中的短期数据时，显式调用 `/forget`。

当前页面采用代码位于 [`chat.js`](../frontend/stage2c/chat.js#L169)；API 实现在 [`api.py`](../app/stage2c/api.py)。Stage2B 的计算结果独立于 B 的模型调用次数。

## 接口清单

| 接口 | 请求/响应 | 模型/地图请求 | 已采用状态会不会改变 |
|---|---|---|---|
| `GET /api/stage2c/status` | 不需要 body；返回配置状态、provider/model、wire 和 command contract 版本、最大模型调用数和节流配置 | 0；不做实时探测 | 否 |
| `POST /api/stage2c/chat` | `ChatRequest`；返回 acknowledgement、questions、slots、preview、interpretation、ticket、base_revision、catalogue_build_id、usage 和诊断 | 每轮最多 2 次 Gemini：INTERPRET 一次，若有 generic 候选再 SELECT 一次。指定地点可能另做 OneMap 地名查询，该计数不在 Gemini `usage` 里 | 否 |
| `POST /api/stage2c/resolve` | `ResolveRequest`：ticket、session_id、revision、choices(slot_id→candidate_key)；返回新 Draft/Preferences、changes、requirement outcomes、阻断、base_revision 和 `requires_client_apply=true` | 0 次 Gemini；读取本地目录 | 否；必须由浏览器随后应用 |
| `POST /api/stage2c/verification` | `VerificationRequest`：verification_ref、session/revision、Draft、Preferences；返回更新后的 Preferences 和 matched/unknown reason | 0 次 Gemini；只查进程内已有核验记录 | 否；浏览器应用返回的 Preferences |
| `POST /api/stage2c/forget` | `ForgetRequest`：session_id；返回本地清理状态 | 0 次 | 不改行程；清理该 session 的 B 短期 ticket、turn 和 verification |

配置页的 `configured=true` 只表示设置可读，不证明 Gemini 额度、模型或网络可用。模型请求只在用户明确发送时发生。`usage.generation_requests` 只数 Gemini，不统计 OneMap，也不统计 Stage2B 路线计算。

## 请求和响应示例

下面 JSON 是协议形状示例。地点、会话和原话均为虚构值。

### chat

```json
{
  "session_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "revision": 0,
  "message": "在 Bugis 附近找咖啡馆",
  "draft": {"visits": []},
  "preferences": {"preferred": [], "excluded": [], "notes": [], "active_constraints": []},
  "pending": null,
  "consent": true
}
```

响应关键字段示意（省略候选的来源、地址、营业参考等详情）：

```json
{
  "acknowledgement": "已找到咖啡馆候选，请确认具体地点。",
  "questions": [],
  "base_revision": 0,
  "catalogue_build_id": "local-build-id",
  "ticket": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "slots": [{"slot_id": "s0", "operation": "add_visit", "selected_key": "s0c0", "options": [{"candidate_key": "s0c0", "identity": "onemap:1.3:103.85", "label": "Mock Cafe", "point": {"label": "Mock Cafe", "latitude": 1.30, "longitude": 103.85, "source": "onemap_search", "confirmed": true}}]}],
  "preview": {"can_apply": true, "ready_to_calculate": false, "adoption_blockers": [], "unresolved_slots": []},
  "usage": {"generation_requests": 2, "calls": []},
  "prompt_version": "2C1-F9.1",
  "wire_version": "2C1-F9.1",
  "command_contract": "operation_scoped_commands_v1"
}
```

实际 `slots` 会含真实目录候选及 `candidate_key`。空候选槽不能被跳过或部分采用。模型推荐的 candidate 只是建议；用户仍需逐槽选择。

### resolve

```json
{
  "session_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "revision": 0,
  "ticket": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "choices": {"s0": "s0c0"}
}
```

成功结果包含 `draft`、`preferences`、`can_apply`、`ready_to_calculate`、`requirement_outcomes`、`adoption_blockers`、`base_revision`、`generation_requests:0` 和 `requires_client_apply:true`。浏览器只能在上述检查成功后把 Draft 和 Preferences 一起应用；B 不直接写浏览器 revision。

## 稳定错误和 HTTP 状态

所有 B 生成的错误都带 `detail.old_itinerary_unchanged=true`。错误诊断只允许安全字段路径/类型，不返回完整模型输入、provider 原文、密钥或异常全文。

| HTTP | 常见 code | 前端动作 |
|---|---|---|
| 422 | `SEMANTIC_COMMAND_MISMATCH`、`AMBIGUOUS_REQUIREMENT_SCOPE`、`CONFIRM_PLACE`、`UNKNOWN_CHOICE`、`HARD_REQUIREMENT_CONFLICT`、`EXCLUDED_VISIT_REMAINS`、`REQUIREMENT_NEEDS_CLARIFICATION`、`UNTRUSTED_PLACE`、`INVALID_PROPOSAL` | 保留原 Draft/Preferences；展示具体提示，修改用户输入或槽选择后重新生成/resolve |
| 403 | `CONSENT_REQUIRED` | 显示发送说明和同意控件状态；不发模型请求 |
| 409 | `CHAT_BUSY`、`CANCELLED`、`PROPOSAL_EXPIRED`、`PROPOSAL_STALE`、`CATALOGUE_CHANGED`、`INVALID_RESOLUTION` | 旧提案作废；按当前 revision 重新发送并重选候选 |
| 429 | `CHAT_CAPACITY`、provider `RATE_LIMIT` | 等待服务空闲或额度恢复；B 不自动重试 |
| 502 | provider 不可用/拒绝结构、`MODEL_SCHEMA`、`SELECTION_SCHEMA`、`INVALID_SELECTION` | 保留原状态；导出不含原话/地点/坐标的诊断 |
| 500 | `LOCAL_CHAT_ERROR` | 原状态不变；保留安全诊断并检查本地日志 |
| 422 (FastAPI) | 请求体类型、必填字段或模型约束不合法 | 修正 JSON 请求，不重新发送未经确认的数据 |

其他稳定 B 错误码包括：`AMBIGUOUS_ORIGIN`、`CONFLICTING_EDITS`、`DUPLICATE_SUGGESTION`、`INVALID_POSITION`、`MODE_NOT_CONNECTED`、`NOTE_TOO_LONG`、`POSSIBLE_SECRET`、`PREFERENCE_CONFLICT`、`SEMANTIC_CONFLICT`、`TOO_MANY_LOOKUPS`、`TOO_MANY_VISITS`、`UNKNOWN_VISIT`、`UNSUPPORTED_EDIT_EVIDENCE`。以 `engine.py` 中 `ChatError.code` 为准。

## 并发、过期、取消和重试

- Ticket 保存完整本地提案，默认 600 秒过期，容量 128；进程重启、过期或淘汰后需重新发送。
- Ticket 同时绑定 `session_id`、`revision` 和目录 build。resolve 版本不匹配会拒绝旧提案。
- 相同 session 同时只允许一个 chat；默认最多 2 个 session 并发。状态页显示配置的值，不代表服务商配额。
- provider 调用间隔默认 2 秒，可由 `PLAYMAP_LLM_MIN_INTERVAL_S` 配置。429、schema 错误和取消都不自动重试、不换模型。
- 用户取消等待时，已发送的 HTTPS 请求可能仍计入服务商用量；前端忽略迟到响应，不采用旧 proposal。
- `/resolve` 不消耗模型调用。重复 resolve 在同一有效 ticket/revision 下可能返回相同结构；前端必须只应用一次并推进 revision。
- `verification_ref` 默认也存活 600 秒；只有同 session、revision、Draft fingerprint 和 catalogue build 匹配时才恢复状态。变化时变为 unknown/未核验。
- `/forget` 只清理 B 进程内记录，不要求删除 Google 已收到的数据，也不会自动清掉第三方服务商记录。

## C 的后续对接点

本次 B 交接没有调用或合并 `c/constraint-aware-alternatives-v1`：

- `/recommend-nearby` 的步行时长范围搜索仍是待联调能力；B 当前把 `max_walk_minutes` 交给已存在的路线检查，不会据冲突自动重新找站点。
- `/alternatives` 的全路线候选验证仍未接入 B。
- 站点细类、局部偏好和约束的传递需要 C/B 联调后另行验收。

所以“每段步行超过用户上限”仍会作为路线核验冲突显示；B 不会声称已经自动搜到满足该时限的候选。
