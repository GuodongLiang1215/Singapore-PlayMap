# Person B：对话语义解析与约束记忆工作计划

## 1. 目标和边界

本计划以 commit `51fa558` 为基线，周期为 14 个工作日。B 的交付物是一条可评估、可追踪的链路：

```text
用户话语
→ semantic_segments
→ 确定性校验与 constraint ledger 更新
→ operation-scoped commands
→ 候选检索 / Pending proposal
→ 用户选择与 Adopt
→ C 的路线和时间计算
```

## 0. 当前实现进度

首个实现切片已经完成：

- `SemanticSegment`、`ActiveConstraint` 和严格的 provider schema 已加入；
- `Preferences.active_constraints` 已接入 engine 和前端往返；
- segments/commands 一致性检查会在不安全时失败关闭；
- opening-hours 要求会保持 `unresolved`，不会被 LLM 标成已满足；
- add/replace 两条路径都能识别 takeaway；
- 候选已携带建议时长、营业时间来源、数据来源和入口数量；
- 已有 22 个自动化测试通过，另有 10 个单轮和 3 个多轮合成 seed cases；
- 完整的 50+10 gold dataset、阈值实验和错误分析仍按后续工作日推进。

B 负责：

- 自然语言中的操作、地点、时间、空间、营业和偏好约束解析；
- 多轮状态中的约束保留、覆盖和撤回；
- 第二站替换、外带、保留其他站点等修改意图；
- 结构化 commands 生成和本地一致性校验；
- 冲突、未知、未支持条件的安全处理；
- 解析准确性和多轮行为的实验评估。

B 不负责：

- 创造目录中不存在的地点或坐标；
- 把 Overture、OSM 或旧 STB 资料自动变成已核验事实；
- 自行判断路线是否满足营业时间；
- 在用户确认前修改 Draft；
- 训练一个脱离 PlayMap 请求链路的独立分类模型。

## 2. 已确认的领域模型

### 2.1 Semantic segment

每个 segment 使用以下字段：

| 字段 | 必要性 | 规则 |
|---|---|---|
| `type` | 区分操作、约束、偏好、保留和冲突 | 只允许有限枚举 |
| `name` | 为约束提供稳定匹配键 | 例如 `place_type`、`opening_hours`、`max_walk_minutes` |
| `value` | 保存用户要求的实际值 | 不把模型推测写入用户值 |
| `scope` | 防止局部要求污染全局 | 例如 `global`、`visit:2`、`time_window:1` |
| `hardness` | 决定冲突处理顺序 | `hard`、`soft`、`unknown` |
| `status` | 区分理解结果和验证结果 | `understood`、`unresolved`、`satisfied`、`conflicting`、`unsupported` |
| `source` | 区分用户、模型、目录和路线来源 | `user_explicit`、`inferred`、`catalogue`、`route` |
| `quote` | 支持原话审计和引用校验 | 必须是用户消息或 pending 消息的精确子串 |
| `turn_index` | 支持多轮覆盖和冲突追踪 | 由应用轮次生成，不由 LLM 自由填写 |

Baseline 不使用未经校准的 LLM `confidence` 作为决策依据。

### 2.2 记忆对象

| 对象 | 作用 | 写入规则 |
|---|---|---|
| `Draft` | 已采用的行程 | 只有 Adopt 成功后更新 |
| `Preferences` | 跨站点的偏好、排除和 `active_constraints` | 用确定性 reducer 更新 |
| `Pending` | 尚未采用的 proposal | 不能改变 Draft 或 active constraints |
| `constraint ledger` | 约束来源、状态、轮次和冲突 | 用于记忆、诊断和实验，不直接绕过 Adopt |
| `revision` | 并发和过期草案保护 | 继续复用现有字段 |

## 3. 代码改动清单

### 第一步：模型和 wire contract

目标：让模型输出 segments，同时保持现有 commands 兼容。

- [`app/stage2c/models.py`](../app/stage2c/models.py)
  - 增加严格的 `SemanticSegment`、`Constraint` 和 `ActiveConstraint` 模型；
  - `Interpretation` 增加 `semantic_segments`；
  - `Preferences` 增加 `active_constraints`；
  - 保留现有五类 `Category`；细分类意图继续放在关键词、偏好或约束中；
  - 保持 `extra='forbid'`，避免模型悄悄添加未定义字段。

- [`app/stage2c/command_contract.py`](../app/stage2c/command_contract.py)
  - 在 provider schema 中加入 segments 对象；
  - 继续使用 operation-scoped commands；
  - 不把营业时间直接加入可执行 command，除非 C 提供确定性检查接口。

- [`app/stage2c/wire_schema.py`](../app/stage2c/wire_schema.py)
  - 同步 provider-facing schema；
  - 限制 segment 类型、状态和来源的枚举值。

### 第二步：Prompt 和上下文工程

- [`app/stage2c/prompts.py`](../app/stage2c/prompts.py)
  - 固定上下文顺序：系统规则 → Draft → Preferences → unresolved/conflicting ledger → Pending → 最新用户消息；
  - 明确五种 segment 类型：`operation`、`constraint`、`preference`、`preserve`、`conflict`；
  - 明确 hard/soft/unknown 的语言规则；
  - 明确餐厅、咖啡馆、菜系、清真、素食、安静和外带的细粒度保存方式；
  - 要求每个 hard segment 有 command 或澄清问题；
  - 要求模型不能把候选描述、营业显示文字或建议时长当作已验证事实；
  - 保留现有“候选只能使用 candidate_key”的规则。

### 第三步：解析、ledger reducer 和一致性检查

- [`app/stage2c/operation_adapter.py`](../app/stage2c/operation_adapter.py)
  - 校验 segments 的枚举、scope、quote 和 turn index；
  - 把 provider 输出转换为严格的本地模型；
  - 拒绝 segment/command 混用、未知字段和不连续顺序。

- [`app/stage2c/engine.py`](../app/stage2c/engine.py)
  - 在 retrieval 前运行 segment/command consistency check；
  - 实现 `active_constraints` 的确定性 reducer；
  - 对同一 scope 的新 hard 约束执行覆盖、撤回或冲突判定；
  - 失败时返回 `SEMANTIC_COMMAND_MISMATCH` 或对应诊断，原 Draft 不变；
  - 修复 `TAKEAWAY_WORDS` 只覆盖 add_visit 的问题，使 replace_visit 也能更新 `stay_profile`；
  - 把 semantic trace、prompt version、catalogue build ID 写入安全诊断。

- [`app/stage2c/catalogue.py`](../app/stage2c/catalogue.py)
  - 候选 payload 增加细粒度搜索标签、`suggested_duration` 及其 basis、`opening_hours_display`、来源和入口数量；
  - 继续把五类顶层类别用于检索；
  - 不在 B 中把营业时间转成 hard feasibility；
  - 保留 Overture 第三方来源和 OSM 参考来源说明。

- [`app/stage2c/api.py`](../app/stage2c/api.py)
  - 传递和返回 `active_constraints`；
  - 错误响应只保留安全诊断，不返回原始 provider body 或完整用户日志。

- [`frontend/stage2c/chat.js`](../frontend/stage2c/chat.js)
  - 让 `Preferences.active_constraints` 随每轮请求往返；
  - Pending 和 Adopt 行为不变；
  - 不要求 UI 直接展示完整 segment JSON。

### 第四步：测试夹具和实验记录

新增：

- `tests/test_semantic_segments.py`
- `tests/test_constraint_ledger.py`
- `tests/test_multiturn_edits.py`
- `tests/fixtures/person_b_semantic_cases.jsonl`
- `tests/fixtures/person_b_multiturn_cases.jsonl`

每条 fixture 至少包含：

```text
case_id
language
turns
gold_semantic_segments
gold_commands
gold_active_constraints
expected_questions
expected_status
```

## 4. 营业时间和数据字段的处理边界

A 已提供 `suggested_duration`、`opening_hours_display`、来源和核验说明。B 只把这些信息作为候选证据传给 selection prompt，并在解释中保留来源；C 负责使用地点时长和路线计算。

对“下午两点必须营业”这类要求，B 生成：

```text
name=must_be_open
hardness=hard
status=unresolved
source=user_explicit
```

在 C 仍返回 `opening_hours_checked=false` 的阶段，系统不能把它变成 `satisfied`，也不能声称路线可行。这个限制必须进入实验的错误分类。

## 5. 五级评估设计

### 单轮问题：50 条

每个尺度 10 条，50 条单轮样例中安排中文 20 条、英文 20 条、中英混合或口语化表达 5 条，剩余 5 条专门用于边界和错误复现。每条都标注 segments、commands、约束状态和期望澄清问题。

1. **简单地点查询**：某地附近某类地点、营业时间、咖啡馆或餐厅细分类别。
2. **单次修改**：第二站替换、保留第一站、外带、排除博物馆。
3. **多站点约束**：多个地点、总时长、每段步行不超过 10 分钟。
4. **分时段分区域**：上午 Bugis、下午 Bayfront，不同区域和时间窗口。
5. **开放式攻略**：首次来新加坡，要求自然、历史、本地餐饮，再通过多轮修改地点。

### 多轮对话：10 组

重点覆盖：

- 第二站替换后类别是否保留；
- add_visit 和 replace_visit 是否都保留 takeaway；
- 新 hard 约束是否覆盖旧 hard 约束；
- 用户明确撤回约束后 ledger 是否清理；
- 营业时间未知时是否保持 unresolved；
- 冲突或错误后 Draft 是否完全不变；
- Pending 未确认时是否不会提前修改路线。

### 指标和门槛

| 指标 | 目标 |
|---|---:|
| schema 校验通过率 | ≥ 98% |
| 明确约束 segment micro-F1 | ≥ 0.90 |
| hard 约束保留率 | 100% |
| segment/command 一致率 | 100% |
| 错误或冲突后的静默修改 | 0 |
| 无证据却声称营业或已验证 | 0 |
| 多轮目标站点识别准确率 | ≥ 95% |
| 外带新增与替换识别率 | ≥ 95% |

## 6. 14 个工作日安排

| 时间 | 任务 | 完成证据 |
|---|---|---|
| 第 1 天 | 锁定 `51fa558`、阅读 A/C handoff、确认数据 build ID | 基线记录和版本清单 |
| 第 2 天 | 建立 glossary、ADR、segments/constraints 模型草案 | Pydantic 模型和 schema review |
| 第 3–4 天 | 修改 prompt、wire schema 和 operation adapter | 解析 fixture 通过 |
| 第 5–6 天 | 实现 active constraint reducer 和状态转移 | ledger 单元测试 |
| 第 7–8 天 | 接入 engine、consistency check、外带替换修复 | add/replace 多轮测试 |
| 第 9 天 | 接入候选证据字段和来源标签 | selection payload 检查 |
| 第 10 天 | 接入安全诊断和前端 preferences 往返 | 错误后 Draft 不变 |
| 第 11–12 天 | 完成 50 条单轮和 10 组多轮 fixture | 自动评估报告 |
| 第 13 天 | 跑中英文和混合语言实验，分类错误 | 指标表和错误样例 |
| 第 14 天 | 修复高频问题，整理演示和报告材料 | commit、测试结果、demo script |

## 7. Definition of Done

- `51fa558` 上的现有 Stage2C 行为没有被破坏；
- segments、commands 和 active constraints 都有严格 schema；
- segment/command 不一致时不会修改 Draft；
- add_visit 和 replace_visit 都能正确处理 takeaway；
- 多轮替换不会丢失未修改的地点或约束；
- 营业时间缺失时只输出 unresolved/unknown；
- 50 条单轮和 10 组多轮 fixture 可重复运行；
- 报告同时给出准确率、约束保留率、错误率和失败案例；
- 生产诊断不包含完整原话、地点名、坐标或 API 凭据。

## 8. 主要风险

- `semantic_segments` 会增加 Gemini 输出 schema 的复杂度，因此必须保留严格上限和失败关闭；
- active constraints 往返需要前端继续传递 Preferences，不能只改后端；
- OSM 营业时间和旧 STB 资料只能作为有来源的参考，不可作为无条件 gold truth；
- C 尚未完成营业时间确定性验证前，B 不能宣称开放时间约束已满足；
- 18,681 条目录会放大候选错误，实验必须单独统计检索错误和 LLM 解析错误。
