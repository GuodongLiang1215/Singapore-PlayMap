# Person B finalization 验收记录

记录日期：2026-10-28。验收对象是 `b/dialogue-semantics-v1` 上基于起始提交 `e516f3359f5e734b48394e1cb56887ab5b0a18c7` 的工作区实现，API/prompt 版本 `2C1-F9.1`，operation contract `operation_scoped_commands_v1`。以下检查在提交、推送或部署之前完成。原始人工复核 JSON/CSV 未修改，也未进入本文件。

## 结论

**B 的本地工程收尾和接口文档已完成；模型解析质量尚未验收。** Python/Node 回归、F7/F9 本地 fixture contract 检查和人工复核导入 dry-run 均通过。没有调用 Gemini 或 OneMap，也没有声称 browser/C/D 端到端验收通过。

## 与最初六项工作的对应

| Person B 责任 | 当前状态 | 判断依据 / 边界 |
|---|---|---|
| 1. 自然语言解析 | 工程入口可用；准确率未验收 | strict schema、字段/scope/quote 本地检查已实现；缺新的独立人工 gold 和模型输出 |
| 2. 多轮对话和约束记忆 | 本地状态更新已修复并通过回归 | Pending 累积与裁剪一致；稳定 visit ID；集合成员撤回、soft/hard、站点时长和核验来源都有行为测试 |
| 3. 理解用户修改意图 | 本地命令链路已收尾 | replace/remove/move/set_stay、保留语义和外带均经一致性/保留检查；proposal 未确认不改已采用状态 |
| 4. 输出结构化规划指令 | 可交给前端/组员 | operation-scoped commands、候选槽、resolve ticket 及输入输出已记录在 I/O 和 API 文档 |
| 5. 错误或冲突指令处理 | 本地安全行为通过 | 错误、过期 ticket、候选缺失、硬约束冲突、来源版本改变都不覆盖已采用状态 |
| 6. 可靠、精确的语义分割 | baseline 实现已完成；效果尚未验收 | 此处“语义分割”是把对话拆成 operation/constraint/preference/preserve/conflict，不是图像分割；可靠性必须等人工答案冻结和模型实验 |

## 本次关闭的问题

| 问题 | 修复后的行为 |
|---|---|
| 清除一个集合成员误删同组其他值 | `clear` 带成员只撤该成员，null 才清当前字段和 scope；主题/排除集合共用规则 |
| soft clear 删除旧 hard 要求 | hard 状态保留；软偏好不会撤销硬约束，也不会进入硬排除过滤 |
| `set_stay` 改 Draft 后账本仍是旧值 | 同一提案同步更新 visit-scoped `stay_minutes`；参数一致性检查逐站核对 |
| 外带绕过 command/preserve 检查 | 作用域外带意图先编译为本地位置/停留动作，再和其他命令一起检查、组装；保留整站时不能旁路改外带 |
| 局部排除变全局排除 | live parser 只接受 global/future_visits；兼容视图也不会把 soft/future/local 意图扩成全局硬过滤 |
| 未知地点类型跳过名称排除 | 已知来源细类使用分类证据；未知类型继续按名称/标签检查，不会静默放行 National Museum 一类名称 |
| SELECT 对不同站点共享偏好 | 每槽仅收到 global、目标 visit 和新增站 future-only 的适用要求 |
| evaluator Pending/确认逻辑与页面不同 | replay 累积最近 3000 字符；questions 不再一律阻止确认，严格 assemble 负责判定实际 blocker |
| 核验来源覆盖原意图来源 | 新状态将 `source` 留给 user/inferred，把 route/catalogue 写入单独 `evidence_source`；旧来源不伪造补齐 |

## R01–R30 覆盖记录

状态只说明本地实现和替身证据，不代表真实 Gemini 准确率、部署或浏览器端到端通过。

| 编号 | 本地证据 | 结果与边界 |
|---|---|---|
| R01 | `test_semantic_wire_contract.py`、`test_f9_dialogue_state.py`、`test_verification.py` | 通过：新输出不能自报 satisfied 或核验来源。 |
| R02 | 同上及 `test_live_provider_permissions_are_stricter_than_saved_state` | 通过：必填字段、quote、scope、source 和 turn_index 由本地入口校验。 |
| R03 | `test_ambiguous_new_visit_scope_cannot_silently_drop_a_hard_requirement` | **部分**：多新增站歧义会拒绝；对已有站越界如 `visit:9` 缺专门测试和目标级提示。 |
| R04 | `test_soft_preference_does_not_erase_an_older_hard_constraint`、`test_soft_parameter_change_cannot_replace_an_older_hard_executable_limit` | 通过：定性少走路不撤步行分钟上限。 |
| R05 | `test_explicit_clear_updates_both_draft_and_ledger_without_mutating_inputs`、`test_cancelling_walk_limit_clears_the_executable_draft_limit_after_adoption` | 通过：明确撤回同步更新 Draft 和账本。 |
| R06 | `test_conflicting_hard_constraints_are_kept_as_conflict`、`test_a_soft_alternative_in_the_same_turn_cannot_replace_or_conflict_with_hard_value` | 通过：同轮 hard 冲突保留，soft 值不覆盖 hard。 |
| R07 | `test_structured_themes_accumulate_and_replaying_the_same_update_is_idempotent`、`test_new_message_turn_increases_without_adopting_a_new_revision` | 通过：更新可重放，消息轮次与已采用 revision 分开。 |
| R08 | `test_an_unrelated_question_does_not_verify_or_hide_an_accessibility_requirement` | 通过：无关问题不能处理或验证无台阶要求。 |
| R09 | `test_chat_adoption.cjs` proposal-visible 测试 | 替身通过：候选和模型预选不会自动 apply；未做浏览器手工走查。 |
| R10 | Node 重复确认测试及 `/resolve` 零模型调用断言 | 替身通过：确认只应用一次，resolve 不调用 Gemini。 |
| R11 | Node 过期 ticket、revision 和迟到响应测试 | 替身通过；未测真实浏览器网络竞态。 |
| R12 | Node 取消、失败和继续澄清测试 | 替身通过：原行程与已采用要求保持；未测真实页面。 |
| R13 | `test_park_discovery_keeps_the_fine_type_instead_of_all_nature` | 通过：受控目录中 park 细类过滤不退化为全部 nature。 |
| R14 | 餐厅/咖啡馆检索测试（`test_f9_retrieval.py`） | 通过：受控目录按细类筛选；不证明全岛 POI 覆盖率。 |
| R15 | 硬关键词 AND、半径锚点和 geocoder 过滤测试（`test_f9_retrieval.py`） | 通过：关键词不互相替代，Bugis 空间意图不当作地点名称。 |
| R16 | `test_each_new_visit_requirement_binds_to_a_different_reserved_identity` | 通过：新增站的局部要求绑定各自预留 ID。 |
| R17 | 站点重排测试及 `m10_reorder_keeps_identity` replay 测试 | 通过：要求跟随 visit ID；来源为合成开发 fixture。 |
| R18 | `test_selection_context_keeps_visit_preferences_on_their_own_candidate_slot` | 通过：不同候选槽收到各自适用偏好。 |
| R19 | `test_takeaway_replacement.py`、外带取消测试 | 通过：新增/替换/取消经同一待采用动作路径；真实解析率未测。 |
| R20 | `test_future_only_exclusion_preserves_an_existing_museum_visit` | 通过：future-only 排除不删除已有地点。 |
| R21 | Node 账本显示、提案取消及 proof 失效测试 | 替身通过；无真实视觉检查。 |
| R22 | offset 等价测试、Node 新加坡时区显示测试 | 通过：同一时刻规范化，浮动时间不补日期。 |
| R23 | `test_zero_minutes_is_a_valid_visit_stay_value`、未注明日期 deadline 与软步行测试 | 通过：0 分钟有效；不凭“六点前”猜日期，不把少走路当作撤销。 |
| R24 | unsupported/accessibility、multi-visit scope 与 F9 合成 fixture | **部分**：本地保留未支持项；分区分时的模型解析与真实计划仍未验收。 |
| R25 | `tests/test_verification.py` 当前 Draft 核验生命周期测试 | **部分**：本地证据绑定/恢复有替身覆盖；未集成 C 的 alternative/routing 新接口。 |
| R26 | 核验过期、目录变更、条件变更、跨会话测试 | 通过本地内存记录行为；不证明服务重启后的持久恢复（记录本来就不持久）。 |
| R27 | `test_person_b_review_import.py` 及 dry-run | 通过：哈希/ID/决定核对；25 条确认，46 条未确认，原件未改。 |
| R28 | review import revise/discuss 与逐话轮确认测试 | 通过：文字讨论和多轮局部确认不会自动提升整组 gold。 |
| R29 | `test_person_b_evaluation.py` 缺失、格式错、重复/陌生 ID 测试 | 通过：失败留在分母，未知或重复预测被拒绝。 |
| R30 | 历史 F7 policy 与 F9 mode evaluator 测试 | 通过：显式区分历史与当前规则；重放不是新模型成绩。 |

### 五个尺度的受控覆盖

F9 合成候选 fixture 有 21 个单轮案例，尺度 1–5 分别为 **5 / 6 / 4 / 3 / 3**；另有 3 组多轮、7 个话轮。fixture contract 检查确认结构与引用格式，不运行 Gemini，也不证明答案正确。组件测试覆盖了检索、scope、时间、候选确认和未知状态，但本次没有满足计划中的“每个尺度完整跑正常分支和未知/冲突分支”的端到端验收。

| 尺度 | fixture 数 | 当前证据 | 未完成部分 |
|---|---:|---|---|
| 1. 单个地点、附近/半径、营业时间 | 5 | radius、细类和营业未知行为的本地测试 | 无真实模型输出；未验证真实 POI 营业覆盖 |
| 2. 修改既有站点、保留/外带 | 6 | reducer、替换、外带与 Node 提案行为测试 | 无浏览器手工走查 |
| 3. 多站路线、总预算和步行上限 | 4 + 3 组多轮 | 时间参数、route evidence、visit ID 和 replay 测试 | 不接 C 替代搜索；无完整路线冲突端到端测试 |
| 4. 不同区域和活动时段 | 3 | scope/unsupported 状态本地测试 | 无逐时段排程能力或真实流程验证 |
| 5. 开放式游玩攻略 | 3 | 主题累积、缺信息和 unsupported 行为测试 | 无独立人审 gold 或模型质量评估 |

## 执行证据

| 检查 | 结果 | 覆盖范围 |
|---|---:|---|
| `.venv/Scripts/python.exe -m pytest -q --tb=short` | **180 passed** | 全部 Python 回归；含状态 reducer、候选检索、外带和确认事件；含合并前新增的 10 项回归 |
| `node tests/test_chat_adoption.cjs` | **23 passed** | 前端 proposal/确认/取消/过期/迟到响应和核验失效行为替身 |
| 默认 F7 fixture 检查 | **60 cases / 71 turns / 0 model calls** | 历史契约检查，不计算新模型质量 |
| F9 fixture 检查 | **24 cases / 28 turns / 0 model calls** | 修订候选合约检查；仍为 `synthetic_unreviewed` |
| 人工复核导入 dry-run | **25 confirmed / 46 unconfirmed** | 读取 CSV 决定后再核对应 JSON；未写出新 gold、未自动晋升 |
| `git diff --check` | 通过 | 有 Windows 工作区 LF/CRLF 提示，没有 whitespace error |

F7 fixture SHA256：`1eef5f436972351e3cae714f295875d201e281aa9faeafe6b7c39893ae91599b`。F9 fixture SHA256：`dd8f72d720cd676e4229778a501a5e186588d46910c6796918965f1c4f922d39`。人工复核原 JSON/CSV 和历史预测 hash 已在 dry-run 输出中核对；正文不包含原话或密钥。

合并前复查补充（2026-10-28）：修复软性外带 set/clear 通过动作编译取消硬约束的问题；软性外带只记入语义状态，不编译为停留修改，外带账本保留解析来源。将按操作计算排除项的逻辑共用于本地检索、OneMap 候选过滤和 SELECT：`future_visits` 只约束新增站点，替换站点和起终点不受误伤。新增 10 项离线回归，全量 Python 180 项和 Node 23 项通过；没有发起真实模型或地图 API 调用。

## 模型效果状态

当前没有新 F9 Gemini 输出，下面这些指标**没有本轮结果**：schema 通过率、明确要求 F1、hard recall、目标站点准确率、外带识别率和真实自然语言错误承诺率。28 轮修订 fixture 仍是基于已看过复核文字形成的开发候选，不是独立 gold。71 轮 holdout 仍未冻结逐字段答案。

唯一真实模型结果仍是 F7 历史基线：71 次解释请求、70 次有输出、1 次限流、无自动重试；历史 strict segment F1 为 0.396、schema 通过率 92.96%。其契约和标注版本不同，不能拿来声称 F9 提升或退步。原目标阈值保持：schema ≥98%、明确约束 F1 ≥0.90、hard 召回 100%、segment/command 一致 100%、目标站点 ≥95%、外带新增/替换各 ≥95%、静默修改和无证据核验宣称为 0。

本轮没有新的模型调用授权，也没有消费费用或额度。真实实验须先冻结独立题目、答案、目录和评分器，再确认调用上限；使用人审文字生成出的题目做过调 prompt 后就不能继续作为独立测试集。

## 尚未验收与依赖

- 没有进行实际浏览器手工走查或生产/Render 部署验收。Node 测试只验证前端状态代码，不等于真实浏览器网络联调。
- 没有和 C 新 alternative 接口集成。`max_walk_minutes` 当前由已有路线检查核验；发生超限时 B 不会自动按路段时限找替代 POI。`/recommend-nearby` 的 `max_travel_minutes` 与 `/alternatives` 仍是后续联调项。
- 营业时间、入口、无障碍、饮食、区域边界和 per-activity 时间窗没有被本地数据完整验证。返回未核验/不支持是预期状态。
- 五尺度 holdout 的 71 个新话轮尚未由人类逐字段冻结；不能把 fixture contract 检查称为解析准确性验证。

## 可交接文件与复现

- [I/O 契约](PERSON_B_IO_CONTRACT.md)：数据对象、字段理由、scope、INTERPRET/SELECT 上下文和代码位置。
- [API handoff](PERSON_B_API_HANDOFF.md)：五个 Stage2C 接口、状态责任、错误码、过期和 C 延期项。
- [收尾计划](PERSON_B_FINALIZATION_PLAN.md)：缺口和实施步骤记录。
- [工作计划](PERSON_B_AI_WORKPLAN.md) 与 [评估说明](PERSON_B_EVALUATION.md)：目标、版本、指标和数据状态。

在仓库根目录复现：

```powershell
.venv/Scripts/python.exe -m pytest -q --tb=short
node tests/test_chat_adoption.cjs
.venv/Scripts/python.exe scripts/evaluate_person_b.py --check-fixtures
.venv/Scripts/python.exe scripts/evaluate_person_b.py --check-fixtures --fixtures tests/fixtures/person_b_f9_semantic_cases.jsonl --fixtures tests/fixtures/person_b_f9_multiturn_cases.jsonl
.venv/Scripts/python.exe scripts/import_person_b_review.py
```

**对组员的实际影响：** B 的本地输入/输出和接口现在可以按文档对接；D 可以继续采用 `chat → 用户选候选 → resolve → 客户端 apply`；C 新接口和真实模型准确率仍需另行验收。
