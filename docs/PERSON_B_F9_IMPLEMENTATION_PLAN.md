# Person B F9 实施计划

日期：2026-10-27。代码起点：`e516f3359f5e734b48394e1cb56887ab5b0a18c7`，分支 `b/dialogue-semantics-v1`。

状态：本地实现、回归和交接文档已完成。2026-10-28 的结果见第 18 节及 [最终验收记录](PERSON_B_FINAL_ACCEPTANCE.md)。精确 F9 标注仍待复核；本轮未调用 Gemini、OneMap 或其他外部服务，也未做真实浏览器或 C 接口联调。

本计划承接原 [B 工作计划](PERSON_B_AI_WORKPLAN.md)，规定 F8 之后的实施顺序。原 F7 报告、原始人工复核和原合成标注保留为历史依据。按用户要求沿用现有 `docs` 目录，不为规划新增目录或移动文件。

## 1. 交付目标与范围

交付一条可验证的请求链路：

```text
用户消息 + 已采用状态 + 待确认请求
→ 解析模型输出
→ 本地校验输出权限、引用与作用范围
→ 本地形成统一的要求更新和可执行命令
→ 按粗类别召回、按细类及证据筛选
→ 展示候选和待采用要求
→ 用户明确确认
→ 原子采用 Draft 与 Preferences
→ C 的路线/时间检查
→ 本地写入有证据的验证结果
```

F9 必须修复：自动采用、细类丢失、空值误撤回、状态伪造、无关澄清放行、偏好不能结构化记忆、排除范围不清、时间表示不统一。F9 不承诺补齐 POI 营业/无障碍数据，也不承诺实现 C 尚未提供的逐站时间窗口或区域约束求解。

现有五大 Category、候选 key、visit ID、Draft 结构、revision 与 ticket 继续使用。当前事实见 [models.py:10](../app/stage2c/models.py#L10)、[engine.py:71](../app/stage2c/engine.py#L71)、[api.py:97](../app/stage2c/api.py#L97)。

## 2. 已确认的行为规则

| 编号 | 必须实现的行为 | 复核依据 |
|---|---|---|
| B01 | “找一个”先展示候选；模型预选和候选齐全都不等于用户确认。 | s04 |
| B02 | commands 是可执行操作的唯一来源；操作审计可由本地派生，不要求模型重复。 | s21 |
| B03 | 明确数值修改和保留按明确要求执行；操作 hardness 不主导评分；跨轮明确替换不制造假冲突。 | s14 |
| B04 | 非法目标保留为未解决意图，询问有效目标，不发出可执行动作。 | s20 |
| B05 | park、cafe、restaurant、museum 不能退化为全部顶层类别。 | s02、s03、s23 |
| B06 | 默认不变不产生虚构 preserve；原话为空或仅空白的 segment 不进入新输出。 | s11、s12 |
| B07 | 未知、未提及和明确撤回是不同情况；账本与执行参数一起变更。 | s42、m01 |
| B08 | 已有站点不静默删除；新排除与旧站点冲突时展示差异、确定规则范围。 | s24 |
| B09 | 模型不能写验证证据或 satisfied；本地验证结果必须绑定当前方案。 | s24、m10 |
| B10 | 新站点用独立编号；绑定 visit ID 后再调序，约束跟随地点身份。 | s01、s21、s31、m10 |
| B11 | 内部保留带偏移时间，界面统一新加坡时间；缺日期不擅自补当天。 | s32、s33 |
| B12 | 咖啡、轻松、室内、亲子、一天等意图按本意保留，不扩大为事实保证或虚构预算。 | s09、s41、s42、s43 |

## 3. 实施拆分、依赖和时间

估计为 B 的 7–10 个开发工作日，按验收推进；等待组员、标注复核或外部调用的时间另计。工期是安排依据，不是完成保证。

| 步骤 | 工作与产物 | 前置步骤 | 估计 |
|---|---|---|---|
| P0 | 固定变更契约、字段表、回归场景和兼容规则 | 已确认的复核意见 | 0.5 天 |
| P1 | 本地模型输出权限、明确撤回、原子要求更新 | P0 | 1–1.5 天 |
| P2 | 候选确认与采用流程、采用前后状态检查 | P1 | 0.5–1 天 |
| P3 | 细类词表、检索编译、空间参照和站点绑定 | P1 | 1–1.5 天 |
| P4 | 结构化偏好、排除范围、活动要求展示 | P1、P2、P3 | 1–1.5 天 |
| P5 | 提示词与上下文、时间和对话输出规范 | P1–P4 | 0.5–1 天 |
| P6 | C 的核验结果接入、证据失效 | P1、P3；C 的实际契约 | 0.5–1 天 |
| P7 | 标注修订工具、离线评分、流程验收和报告 | P0 起准备；P1–P6 后验收 | 1.5–2 天 |

P1–P5 可独立完成。若 C 暂无可用返回，P6 交付明确的 unknown/unsupported 分支及测试，支持项与未支持项分别列出，不伪造通过结果。

## 4. P0：固定契约与回归依据

### 改动位置

- [models.py:19](../app/stage2c/models.py#L19)：解释语义与持久要求的字段。
- [command_contract.py:55](../app/stage2c/command_contract.py#L55)、[wire_schema.py](../app/stage2c/wire_schema.py)：模型输出与本地契约。
- [ADR 0001](adr/0001-dialogue-segments-and-constraint-ledger.md)、[GLOSSARY.md](../GLOSSARY.md)：更新来源、权限、范围与撤回定义。
- [现有 fixture 说明](../tests/fixtures/README.md)：区分历史 F7 标注、F9 修订和独立测试。

### 具体任务

1. 为 B01–B12 建立“输入状态、用户话语、输出意图、执行结果、禁止行为”表；对应第 12 节的 R01–R30。
2. 建立规范字段字典，规定值类型、合法 scope、单值/集合更新方式、执行能力与验证方式。首批覆盖 place_type、max_walk_minutes、takeaway、excluded_topic、theme、drink、pace、environment、walking_effort、duration 及现有时间/交通字段。
3. 明确同名字段不一定同一合并语义：时间上限是单值；theme 是集合；drink=coffee 可作用于本次站点；preserve 默认只约束本轮修改。
4. 将 F9 作为下一次完整契约版本；同步 prompt、wire、前端版本检查与静态文件版本。不可把 F7 输出重放标成 F9 模型实测。
5. 分开消息轮次与已采用 revision。当前 [engine.py:337](../app/stage2c/engine.py#L337) 用 revision+1 写 turn_index，不能区分同一草案上的多次未确认追问；F9 由应用按消息分配轮次，候选选择不另算一轮。reducer 接收确定的本地轮次，重放使用相同事件顺序，不由模型填写。

完成条件：字段、更新方式和能力归属可以直接用于 P1–P7；每项产品规则都有可检查的验收场景。历史数据不被覆盖。

## 5. P1：本地权限与原子状态更新

### 改动位置

[operation_adapter.py:106](../app/stage2c/operation_adapter.py#L106)、[models.py:19](../app/stage2c/models.py#L19)、[semantic.py:47](../app/stage2c/semantic.py#L47)、[semantic.py:61](../app/stage2c/semantic.py#L61)、[engine.py:93](../app/stage2c/engine.py#L93)、[engine.py:207](../app/stage2c/engine.py#L207)。

### 具体任务

1. 分开“接收模型新输出”和“读取保存状态/历史预测”的入口。新输出在本地执行与 provider 相同的权限限制；历史兼容只能由显式版本入口调用。
2. 新输出拒绝 satisfied、catalogue/route 来源、缺少必需字段、空白 quote、语法非法或越权 scope、模型自填 turn_index。语法合法但目标不存在的未解决意图另行保留；若仍生成引用无效目标的可执行 command，则拒绝执行并返回目标诊断。quote 继续要求来自本轮或 Pending 的原话；默认不变不能补造 preserve。
3. 给结构化更新增加明确 effect。set 必须有合法值，clear 表示撤回，none 用于无值操作审计/保留/冲突，不用 null 推断更新意图。旧 null 撤回只在版本明确且有明确撤回证据时兼容；模糊愿望不转换为数值撤回。
4. 将 ledger 更新与 Draft 的步行限制、外带等执行参数先合成一个待采用更新；任一校验失败，二者都不改变。保持准备、预览与采用分离。
5. 保留同轮不相容 hard 值的冲突；跨轮明确替换更新旧值；soft 不能默默清除旧 hard。重复执行同一 reducer 输入不得产生重复条目。
6. 按字段能力逐条生成处理结果。不能再把任意 questions 非空当作所有 hard 要求都已处理；支持参数、缺失信息和未支持能力分别记录。
7. 客户端传回的历史 status/evidence 也不当作证明；没有服务器或校验器可核对的证据就降为未验证，不靠“source=route”通过。
8. 本地消息轮次独立于 Draft revision，继续澄清可增加轮次而不采用状态；同一轮 reducer 重放仍保持确定性，不把未确认追问当作同一条已采用修改。

### 测试与完成条件

扩展 [test_semantic_wire_contract.py](../tests/test_semantic_wire_contract.py)、[test_semantic_segments.py](../tests/test_semantic_segments.py)、[test_engine_semantic_integration.py](../tests/test_engine_semantic_integration.py)。

现有 `FakeClient` 使用 satisfied 作为正常输入，需要改成合法输入，并另建伪造验证的拒绝测试，不能仅删除原测试。验收 R01–R08 全通过，失败后的 Draft、Preferences 与已采用 revision 与请求前完全一致。

## 6. P2：候选确认与采用流程

### 改动位置

[chat.js:54](../frontend/stage2c/chat.js#L54)、[chat.js:93](../frontend/stage2c/chat.js#L93)、[chat.js:139](../frontend/stage2c/chat.js#L139)、[chat_state.js](../frontend/stage2c/chat_state.js)、[api.py:97](../app/stage2c/api.py#L97)、[proposal_status.py](../app/stage2c/proposal_status.py)。

### 具体任务

1. 移除 send 中候选齐全后直接 adopt 的路径。推荐仍可预选，但必须展示并等待用户确认。
2. “找一个”显示候选及“加入行程”；“加一个”展示建议新增的具体地点。两者都复用 Pending/resolve；本轮不新增独立搜索状态机。
3. 只有明确确认触发 resolve 和 bridge.apply；resolve 继续为 0 次模型调用。一次确认只能应用一次，防止连续点击重复新增。
4. 候选变化、旧 ticket、过期 revision、目录版本变化或地图手动修改，都不能用旧草案覆盖当前行程。
5. 明确区分执行信息缺失与等待核验：缺少本次动作必需的目标/日期等信息、存在已知 hard 冲突或没有匹配候选时，留在 Pending；候选已选、动作信息完整且无已知冲突时，可由用户确认暂存未核验 Draft 与要求，并继续计算。某项事实尚无证据或能力不支持，不等于可以说它已满足，也不自动阻断安全暂存。必须保证前端按钮判断与后端 strict 检查一致。
6. can_apply 表示可确认保存草案；ready_to_calculate 表示计算输入完整；二者都不表示所有 hard 条件通过。等待 C 结果时显示“未核验草案/参考路线”；已知失败则显示违反哪项要求并等待修订，不能继续展示为满足要求的最终方案。
7. 路线核验在保存后运行，因此不能要求采用前先取得尚未计算的路线证据。关键缺口由本地逐条生成提示/澄清，模型可补充游玩问题；无关提问不能改变硬要求的未解决状态。

### 测试与完成条件

新增 `tests/test_chat_adoption.cjs`，使用已安装 Node 的内置测试和 vm，运行真实 chat.js 逻辑，用最小 DOM、fetch、bridge 替身检查 apply 次数和状态。测试用户行为及副作用，不通过搜索代码字符串断言实现。当前未安装 Playwright/httpx，本计划不把安装它们作为前置条件。

验收 R09–R12：收到模型响应后 apply=0；选择候选后仍为 0；一次明确确认后 apply=1；resolve/apply 失败时保留原状态与要求。真实浏览器呈现另按第 13 节人工检查，不把 Node 流程测试称为浏览器端到端实测。

## 7. P3：细类检索与作用范围

### 改动位置

[retrieval_rules.py:18](../app/stage2c/retrieval_rules.py#L18)、[catalogue.py:60](../app/stage2c/catalogue.py#L60)、[catalogue.py:127](../app/stage2c/catalogue.py#L127)、[semantic.py:102](../app/stage2c/semantic.py#L102)、[semantic.py:131](../app/stage2c/semantic.py#L131)、[engine.py:355](../app/stage2c/engine.py#L355)。目录依据见 [add_overture_to_catalogue.py:242](../scripts/add_overture_to_catalogue.py#L242)。

### 具体任务

1. 去掉 park 与全部 nature 等价的规则。中英文公园表达映射为 park 细类；nature 仍表示较宽的自然主题。
2. 建立有界细类词表，先处理 park/cafe/restaurant/museum；优先读取 Overture 原始细类与官方来源的明确类型，再读取可解释的 search_tags。名称或地址文字仅可作为辅助，不足以证明硬类型。
3. 从 action 与同 scope 的结构化要求编译本地检索条件。粗类别负责召回，hard 细类负责过滤，soft 兴趣负责排序；具体关键词不能被删掉以凑候选。
4. 本地生成细类过滤条件，不要求模型在 segment 和 command 再复制一套字段。旧 commands 的关键词继续兼容，并检查与新结构化要求有无矛盾。
5. 列表关键词的 OR 匹配不能代替多个 hard 条件的同时满足；素食、清真等无证据属性保留未核验，不因 restaurant 命中就放行。
6. query 只保留用户命名的目标地点；near Bugis 的参照另存为空间要求。半径必须关联中心。可复用 [nearby 接口](../app/stage1c/api.py#L144) 的直线半径能力；不能将其称为步行范围。若输入要求只有区域名而无边界，不擅自补半径。
7. 为每个新增地点分配 `visit:new:N`，预留 ID 后绑定；已采用站点从 snapshot ordinal 解析为 visit ID。同轮替换和调序使用同一身份；不能把替换约束挂到 visit:new。
8. 无效目标的 operation 意图留在解释记录并提出问题，不转成 action，也不加入有效站点约束。可执行 operation 审计由已校验 commands 派生，避免双写。

### 测试与完成条件

扩展现有语义、engine 和候选测试，替换 `test_broad_park_intent_uses_the_existing_retrieval_alias` 的旧宽化预期。验收 R13–R17：在受控目录中 park 不返回纯 beach，restaurant 不返回仅 hawker，未知类型不伪装通过，多新增及调序的约束身份完全正确。

## 8. P4：偏好记忆、排除范围与展示

### 改动位置

[models.py:39](../app/stage2c/models.py#L39)、[semantic.py:61](../app/stage2c/semantic.py#L61)、[engine.py:93](../app/stage2c/engine.py#L93)、[engine.py:192](../app/stage2c/engine.py#L192)、[catalogue.py:25](../app/stage2c/catalogue.py#L25)、[chat.js:38](../frontend/stage2c/chat.js#L38)。

### 具体任务

1. 在现有 active_constraints 中兼容保存结构化偏好，不重命名现有请求字段。增加 kind 区分 constraint/preference，旧记录按 constraint 读取。
2. 用字段字典控制合并：同 scope 的单值明确替换；多主题集合增删；未提及保留；明确撤回才删除；同输入重复合并不重复。
3. drink=coffee 按本次站点保存，不能扩为全局 food 偏好；亲子记录为主题，不写成地点已验证适龄。
4. preserve 默认保护本轮用户明确指出的范围，不升级为永久锁；后续明确修改仍可执行。无关站点由默认不变保护。
5. 把结构化要求作为过滤与冲突检查的共同依据；preferred/excluded 继续保留为兼容视图，不与账本各自维护相反事实。
6. 排除规则沿用 scope 明确 global 或 future_visits。已有站点与全局新规则冲突时展示站点名并询问；用户选“仅后续新增”后才按该范围保存，不静默删除或偷偷改变范围。
7. notes 以结构化要求生成可读摘要；取消外带或变更要求时同步失效。模型补充的非结构化说明仍可保留，但不作为硬约束执行依据。
8. 界面分开显示已采用要求与本轮待采用要求，包括细类、作用站点、hard/soft 和未核验状态；使用产品语言，不把内部 JSON 直接展示给用户。

### 测试与完成条件

扩展语义、engine、外带替换及前端流程测试。验收 R18–R21：多主题不互相覆盖；咖啡偏好作用域正确；外带撤回不留有效旧 notes；新排除不自动删除已有点；取消 Pending 不改变已采用记忆。

## 9. P5：提示词、上下文、时间与对话

### 改动位置

[prompts.py:3](../app/stage2c/prompts.py#L3)、[engine.py:113](../app/stage2c/engine.py#L113)、[engine.py:329](../app/stage2c/engine.py#L329)、[time_updates.py:63](../app/stage2c/time_updates.py#L63)、[stage2b/models.py:49](../app/stage2b/models.py#L49)、[proposal_status.py:16](../app/stage2c/proposal_status.py#L16)。

### 具体任务

1. 上下文只提供一次规范活动要求：当前 Draft、已采用偏好/要求、Pending、最新消息、新加坡当前时间与能力摘要。删除当前 active_constraints 在两处重复携带的内容；保留稳定身份和来源。
2. 能力摘要由本地生成，区分可执行参数、可查询数据、可验证条件和未知能力。模型不能修改这份摘要，也不能用它替代实际验证。
3. 统一规范字段名与中英文别名；operation 不再要求重复生成。无法执行的意图仍须保留，不为追求结构通过而删掉原话要求。
4. 时间内部继续使用带 +08:00 的日期时间并规范到 SGT，界面显示普通本地时间；相同实际时刻允许确定性规范化。只有明确今天/明天或已确定的行程日期才能补全日期。
5. 仅给 18 点且缺行程日期/出发时间时保留未解决要求；一天保留定性时长，少走路保留偏好，不编造分钟预算或上限。
6. 对话先回应游玩目标，再用一句说明关键能力限制，最后问一个能推进任务的问题。不能用“想去哪类地点”当作已解决无台阶或营业硬要求。
7. 可见的保存/采用/计算声明由应用实际阶段生成；没有证据不说“已满足”“保证营业”“全程无台阶”。

### 测试与完成条件

扩展 prompt 合约测试，但不只依赖提示词包含某个词；用给定输出经过真实校验/展示路径验证结果。验收 R22–R24，并人工检查五个尺度的解释与澄清是否保留关键要求。

## 10. P6：接入 C 的结果与证据失效

### 改动位置

[stage2b/planning.py:253](../app/stage2b/planning.py#L253)、[stage2b/api.py:35](../app/stage2b/api.py#L35)、[stage2c/engine.py:284](../app/stage2c/engine.py#L284)、[stage2c/api.py:97](../app/stage2c/api.py#L97)、[models.py:39](../app/stage2c/models.py#L39)、前端采用后计算路径 [chat.js:155](../frontend/stage2c/chat.js#L155)。拟新增共享叶模块 `app/verification.py`，集中保存和读取真实校验结果；不让 B/C 互相导入各自的 engine。

### 具体任务

1. 实施时先核对 C 的实际分支、handoff 和返回样例。当前 B 起点仍有 opening_hours_checked=False；不能将小组消息当作接口已合入。依赖缺失时列出缺口并继续本地工作，不自行替 C 重写规划器。
2. 明确四类结果 pass/fail/unknown/unsupported，分别对应当前约束的核验结果；不能用路线请求成功代替约束通过。
3. C 的服务端 `/plan` 完成真实计算后，将实际输入和可解释检查结果写入有界、带过期时间的 RAM 记录，返回不含坐标的 verification_ref。记录绑定 session、实际采用后的 revision、规范 Draft 摘要、目录版本，以及每项检查所覆盖条件的规范摘要。当前 ProposalStore 存的是采用前提案，不能复用其 revision 当作路线结果。
4. B 通过拟新增的无模型调用读取入口 `/api/stage2c/verification` 核对 ref、session、当前 revision、Draft 摘要及条件值，返回仅更新验证状态的 Preferences。前端在计算后调用该入口；后续 chat 可携带 ref 再校验。C 不必信任客户端传来的 active_constraints，B 必须逐项核对 C 实际检查的条件与用户当前要求；C 只给笼统 opening_hours_checked 标志而缺具体证据时，相关要求仍为 unknown。
5. 新叶模块只提供记录、查询、会话清除三个接口，不新增数据库、凭据或签名配置。RAM 容量与过期采用有界常量和可注入时钟测试；重启、不同进程、过期或记录丢失均返回未验证，不能从浏览器 activePlan 或自报 route 来源恢复可信证明。摘要必须校验，不能只相信相同 revision。
6. 地点、顺序、交通方式、停留、日期/时间和相关目录发生变化，按依赖让相应证据失效。一个约束通过不能代表其他约束或整个行程都通过。
7. 营业与开放状态的展示注明参考来源和适用日期。“按参考数据通过”不等于实时营业保证；没有日期、完整规则或必要数据时为 unknown。

### 测试与完成条件

使用 C 返回样例与本地替身先跑 R25–R26，再做一次 B/C 流程联调。若 C 未提供 opening/accessibility 检查，交付 unknown/unsupported 与明确界面文案；该功能不计为已完成的事实核验。

## 11. 字段与权限清单

每个新增项必须解决已复现的缺口。字段数不能因为解释方便而无限增加。

| 字段/对象 | 用途与必要性 | 谁可写入 | 兼容方式 |
|---|---|---|---|
| segment.effect：set/clear/none | 把明确撤回与无值/未知分开，防止 s42 误删。 | 模型提出，本地按类型与证据校验。 | 旧输出仅在显式历史版本入口读取；新输出不能靠默认猜测。 |
| ActiveConstraint.kind：constraint/preference | 让结构化偏好跨轮保留，避免只存在一次 segments 或自由 notes 中。 | 本地 reducer。 | 旧条目默认 constraint；原 active_constraints 请求键保留。 |
| 规范 scope | 绑定站点身份并区分全局与后续新增排除。 | 模型提出有限语法，本地解析。 | 单新增 visit:new 与 visit:new:1 可等价；多新增必须编号。 |
| 本地细类过滤条件 | 保证 park 等硬类型真正进入筛选，不依赖关键词 OR。 | 检索编译代码。 | 不增加一套模型重复填写的 place_type 字段。 |
| 逐要求的处理结果 | 判断每条 hard 要求是可执行、缺信息还是未支持，阻止任意 question 放行全部要求。 | 本地检查。 | 用户只看精简说明，原 questions 可继续显示。 |
| 核验记录 | 使 satisfied 可追溯且可失效。 | 本地/C 校验器。 | 无记录或版本不符均不能视为已证明。 |
| verification_ref 与方案/条件摘要 | 让 B 读取 C 服务端真实结果，并阻止同 revision 改参数后复用旧证明；采用前 ticket 不具备此用途。 | C/共享本地记录模块；客户端只能回传引用。 | 老请求无 ref 时仍可使用，核验为未知；摘要不交给模型。 |
| 标注修订记录 | 记录从原 gold 到新答案的原因、规则版本与复核状态。 | 导入工具与人工复核。 | 保留原始 JSON/CSV 和 F7 数据，不覆盖。 |

核验记录最小字段：constraint_key 对应要求；validator 表示执行了哪项检查；result 表示结论；evidence_ref 指向实际证据；draft_revision 防止跨草案复用；catalogue_build_id 在引用目录时必填；checked_at 仅在证据具有时效性时需要。要求原有 source 表示用户/推断来源，不改成 route 来冒充验证来源。

不新增 LLM confidence，不新增第二套搜索命令，不把操作 hardness 用作阻止后续明确修改的理由。

## 12. 回归验收矩阵

以下 30 项是必须覆盖的行为场景，不要求刚好写成 30 个测试函数。参数化可合并同类输入，断言应落在公开行为和实际副作用上。

| 场景 | 输入/操作 | 必须观察到的结果 | 负责人步骤 |
|---|---|---|---|
| R01 | 模型输出 satisfied 或 route/catalogue 来源 | 新输出本地拒绝，原状态不变 | P1 |
| R02 | 缺字段、空白 quote、复制上下文 quote、模型写 turn_index、语法非法/越权 scope | 拒绝并给格式诊断，原状态不变 | P1 |
| R03 | 语法合法的 visit:9 指向不存在站点，或约束绑定错误 | 无效目标意图可保留；不产生/不执行错误动作，给目标诊断或询问 | P1/P3 |
| R04 | 已有 10 分钟上限后说“少走路” | 上限两处均保持 10，并增加定性偏好 | P1 |
| R05 | 明确取消、未提及、“不要取消”三种输入 | 仅明确取消同时清除账本和执行参数 | P1 |
| R06 | 同轮不同 hard 数值；后轮明确替换；soft 替代 | 分别为冲突、正常更新、旧 hard 保留 | P1 |
| R07 | 合并失败；重复合并同一轮输入；同 revision 连续追问 | 失败不改变旧状态；同轮重放不重复；消息轮次独立推进 | P1 |
| R08 | 无台阶 hard 要求加一个无关问题 | 不视为该要求已被处理或已满足 | P1 |
| R09 | 找/加一个候选，模型已预选 | 响应与改选候选均不调用 bridge.apply | P2 |
| R10 | 明确确认一次，或连续点击 | 只采用一次；resolve 的模型调用为 0 | P2 |
| R11 | 过期 ticket、revision、目录版本或地图被修改 | 旧提案不能覆盖新状态 | P2 |
| R12 | resolve/采用失败；取消 Pending 后再发新消息；未确认时继续澄清 | 原行程和已采用要求不变；后轮只继承正确 Pending/已采用记忆 | P2/P7 |
| R13 | 公园请求，目录同时有 park、beach、garden | 不能仅因 nature 大类返回非公园；未证实类型不算通过 | P3 |
| R14 | 餐厅/咖啡馆请求，目录含 hawker | 对应细类保留，不能回退到全部 food | P3 |
| R15 | 素食餐厅、近 Bugis 的咖啡馆 | 关键词 OR 不证明素食；Bugis 不被当成目标名称 | P3 |
| R16 | 同轮新增三站及各自要求 | 各绑定不同新 ID；单新增别名只在单新增时成立 | P3 |
| R17 | 替换第二站再调至第一；后轮改当前第一站 | 约束始终跟随对应 visit ID | P3 |
| R18 | 多主题、coffee 局部偏好，后轮只改交通方式 | 主题并存、局部范围不变、旧要求不丢 | P4 |
| R19 | 外带新增/替换后再明确取消 | profile、结构化要求与 notes 一致更新 | P4 |
| R20 | 排除博物馆但保留已有站点，再选仅新增生效 | 不自动删除；规则范围明确并持续生效 | P4 |
| R21 | 查看已采用与待采用要求，撤销/取消草案 | 两组展示正确，无虚构 preserve 或验证声明 | P4 |
| R22 | 带 +08:00 与等价 UTC 的完整时间 | 内部同一时刻，显示新加坡本地时间 | P5 |
| R23 | 仅 18 点、一天、少走路、0 分钟停留 | 不补造日期/预算/上限；0 仍是有效停留值 | P5 |
| R24 | 亲子无台阶、分区域分时段请求 | 关键要求全部保留，能力限制可见，提问推进任务 | P5 |
| R25 | hard 步行/总时长尚无证据，用户确认保存后计算；C 返回四种结果 | 不出现采用/核验循环等待；仅按真实结果更新对应条件，失败保持可修订草案 | P2/P6 |
| R26 | 校验后改参数/目录、同 revision 改 Draft、伪造 ref、换会话、刷新后记录丢失/过期 | 摘要或会话不符时拒绝证明；记录丢失降为未知，不恢复旧 satisfied | P6 |
| R27 | 复核 CSV/JSON 不一致、哈希不符、重复 ID | 导入拒绝，0 个原文件被覆盖 | P7 |
| R28 | revise/discuss 只有文字、仅确认多轮第三轮 | 不自动提升整个 case 或未确认话轮 | P7 |
| R29 | 缺预测、格式错误、重复/陌生 ID、派生 operation | 不退出评分分母；错误数据拒绝；派生项不算模型成绩 | P7 |
| R30 | F7 原输出分别用旧规则与 F9 规则重放 | 版本明确；原报告保留；不能声称新模型性能提升 | P7 |

## 13. P7：标注、评分与实验

### 标注修订

现有 25 条复核覆盖中文 23、混合 2，没有英文。原 JSON 的 confirmed_turns=25，revise/discuss 没有完整替代答案；本轮“全部认可答复”作为规则确认记录，不伪称用户已经逐字段审阅未来生成的新 JSON。

新增 `scripts/import_person_b_review.py`，默认 dry-run：先从 CSV 找到确认话轮，再按 key 读取 JSON，校验基线、fixture/prediction 哈希、决定一致性与 ID。输出修订候选及差异，不覆盖输入，既有输出也不能覆盖。用户已确认的规则可用于起草修订，无需重复问产品规则；精确结构化答案另记录复核状态。

原 71 轮保留为已见开发集。F9 修订结果保存为 `tests/fixtures/person_b_f9_semantic_cases.jsonl`、`tests/fixtures/person_b_f9_multiturn_cases.jsonl`，修订清单保存为 `tests/fixtures/person_b_f9_review_manifest.json`，关联原 ID、规则与逐轮复核状态；不原地改写 F7 fixture。尚未确认的 46 轮不自动转为 human_reviewed。整个多轮 case 只有全部相关话轮完成复核后，才能作为完整人审 case。

### 评分变更

修改 [evaluation.py:23](../app/stage2c/evaluation.py#L23)、[evaluate_person_b.py](../scripts/evaluate_person_b.py)、[test_person_b_evaluation.py](../tests/test_person_b_evaluation.py) 及 [PERSON_B_EVALUATION.md](PERSON_B_EVALUATION.md)：

- 保留原严格统计，另报规范化的语义内容 F1；派生 operation、notes 文本不同不主导内容分数。
- 内容 F1 在冻结前固定为 micro-F1：对 constraint/preference/preserve/conflict 的 `(type,name,value,effect,scope,hardness,source)` 规范元组按重复次数匹配，累计 TP/FP/FN，计算 `2TP/(2TP+FP+FN)`。status 为本地判断、quote 边界另验，不进入内容匹配；effect 必须参与，set/clear/none 不可等价。持久 ledger 比较加入 kind、规范 scope 与本地状态。
- 分别报告细类保留、目标身份、明确撤回、活动记忆和关键缺口处理，避免总分掩盖危险错误。
- 仅允许已证明的等价规范化；park/nature、亲子意向/已验证适龄不等价。
- 缺测与结构失败继续计入失败；调用失败与模型结构错误另列，不能从结构通过率分母中隐去。
- 是否提问与问题是否处理关键缺口分别评估；对话自然度由人工按清单检查。
- 报告分别记录模型输出版本、标注版本、评分规则版本、本地代码与目录版本；旧输出重放不计为 F9 新模型实测。

目标、外带新增/替换、撤回及 hard 保留指标分别固定分母，并要求最终集都有非零覆盖；N/A 不算达标。hard 保留评估 gold 中 effect=set 的明确 hard 约束，clear 的正确执行单列，防止用更换分母掩盖撤回错误。规范化表和评分器在最终模型输出出现前冻结。

### 多轮状态推进

当前 evaluator/collector 的“解释成功后直接合并 Preferences、Draft 用固定快照、Pending 为空”只保留为 F7 历史重放模式，不能用于 F9 应用记忆验收。F9 多轮 fixture 明确每轮 confirm/cancel/continue_pending 事件、受控目录和用户候选选择；这些事实与事件在看预测前固定，不能从 gold interpretation 复制答案。

每次解释先产生 Pending；未确认追问带上 Pending，已采用 Draft/Preferences 不变；确认时使用共同本地 reducer 原子推进两者；取消只清除 Pending。候选与 C 结果由固定替身提供，不调用第二个真实模型或地图 API。若预测不支持脚本中的确认，记录该步骤失败并保留实际状态，不强行注入 gold Draft。确认事件本地执行，不增加解释调用数。

单轮固定快照模式继续作为解析实验单列；不能把它的账本重放结果称为真实确认流程成绩。R12 检查取消与追问，R17 检查调序后的真实状态推进。

### 五个尺度与流程检查

| 尺度 | 代表请求 | 主要检查 |
|---|---|---|
| 1：地点查询 | Bugis 附近 1km、14 点营业的咖啡馆 | 类型/中心/半径/时间保留，先给候选，未知营业不保证 |
| 2：单次修改 | 第二站换咖啡馆、外带、第一站保留 | 目标、profile、局部约束与其他站点不变 |
| 3：多站点约束 | 三站、四小时、每段步行最多十分钟 | 不漏细类，总预算与步行约束分别进入执行/验证 |
| 4：分时分区 | 上午 Bugis 喝咖啡，下午 Bayfront 游玩 | 新站点 scope 正确，不用名称查询冒充空间约束，不假称已满足窗口 |
| 5：开放攻略 | 亲子、本地餐饮、历史自然、轻松一天 | 保留多主题，定性偏好不变虚构硬数值，澄清自然且限制可见 |

受控目录与 C 替身覆盖所有尺度，每级至少一个“能处理”分支和一个“未知/冲突/未支持”分支。浏览器人工走通六条路径：仅搜索、确认新增、替换外带、排除但保留旧站点、模糊偏好不撤上限、分时段/无台阶未核验提示。检查采用前后地图、清单与活动要求，不把解析返回成功等同于流程通过。

### 独立测试与在线调用

提出一套新的最终测试配置：50 条单轮，五尺度各 10 条；标准样本中文 20、英文 20、混合 5，另 5 条边界样本单列。再加 10 组多轮、共 21 轮，总计 71 个解释请求，分别保存为 `tests/fixtures/person_b_f9_holdout_single.jsonl` 与 `tests/fixtures/person_b_f9_holdout_multiturn.jsonl`。它是后续待冻结的测试设计，不是本轮已获授权的调用。

答案须在看到新模型输出前完成复核和冻结。原开发集用于调整提示词，新测试答案不能按模型输出回填。调用前冻结模型、prompt/wire、实现代码、评分器和 fixture 的版本/哈希；若看过最终集结果后用其调整代码或提示词，该集转为开发集，后续重测只能作为探索成绩，独立最终测试须另建未见样本。在线收集前提供费用边界与请求上限，取得新的调用授权后才运行。每轮只一次解释请求，无自动重试；候选选择和地图调用不藏在这 71 次解释请求里，若需要真实流程实验另列预算与调用数。

原已用满的 71 次授权不再复用。计划、标注检查、回归和旧输出重放全部可以不调用外部模型完成。

## 14. 验收标准与报告边界

### 本地实现验收

- R01–R30 全部通过；原 B 回归与相关 Stage2B 空间/时间回归通过。因确认规则变更而改动的旧测试，逐项记录理由。
- 候选展示、改变下拉项和 Pending 澄清造成的未确认 Draft 修改次数为 0。
- 状态伪造、错误目标、过期提案、冲突更新后的静默修改次数为 0。
- 明确撤回、站点身份和偏好范围在受控样例中全部正确；账本与执行字段无分歧。
- 不支持的能力有明确信息，不能靠加入任意问题就变成可交付的已满足结果。
- 用户/C/D 的既有 Draft 与路线输入不被擅自重命名或改造；类型补充不改变 A 的原始 POI 文件。

### 模型与整体验收

沿用原目标：结构通过率 ≥98%，明确约束内容 F1 ≥0.90，hard 约束保留率 100%，segment/command 一致率 100%，目标站点准确率 ≥95%，外带新增与替换识别率 ≥95%，无证据验证宣称与静默修改均为 0。

规范化内容 F1 的定义会改变，因此原 F7 严格 F1=0.396 不能直接当作新指标的对照。须保留同输出/同标注/同评分规则的可比重放，再单独比较新输出。局部通过不能自动表示检索和路线可行性全部通过。

### 验证方式

实施后在仓库根目录运行已有 B 测试文件及新增行为测试，执行 fixture 完整性检查、Python 编译检查、Node 语法检查和差异检查。Node 行为测试命令预定为 `node --test tests/test_chat_adoption.cjs`。F9 修订 fixture 生成后使用以下明确输入，不改变原 F7 文件的默认选择：

```powershell
.venv/Scripts/python.exe scripts/evaluate_person_b.py --check-fixtures --fixtures tests/fixtures/person_b_f9_semantic_cases.jsonl --fixtures tests/fixtures/person_b_f9_multiturn_cases.jsonl
```

验收报告要记录实际命令、环境、通过/失败数、调用次数与未覆盖项。自动测试使用替身，不读取密钥；真实浏览器与 B/C 实测分别记录，不伪称本地替身是在线验证。

## 15. 兼容、提交与回滚

1. 在现有 B 分支按 P0–P7 分批提交，每批包括行为、必要测试与说明；不把数据修订、评分口径和新模型成绩混成同一条“性能提升”。实施期间保留用户原始复核文件。
2. 新服务器接受旧持久记录的明确兼容读取，默认旧 kind=constraint；不让新 provider 输出借历史入口绕过严格校验。旧 Pending 没有新版本字段时使其过期并提示重新生成，不猜测 effect 后执行。
3. 不承诺新状态能直接交给旧服务器。混用页面/服务版本时显式拒绝采用并保留可读的当前行程与要求，不静默清空状态以消除报错。
4. 每批可用对应提交回退；切换版本前保留可恢复的已采用状态。回退遇到新状态不兼容时阻止继续采用，不能丢弃 hard 要求或恢复自动采用来凑兼容。
5. 不改密钥、服务配置、目录结构或 POI 构建，不自动调用外部 API、发送组员消息或部署。后续真实调用、对外操作按既有授权范围执行，缺少授权时单独说明。

## 16. B/A/C/D 对接内容

| 负责人 | 对接内容 | 对 B 的影响 | 缺失时的处理 |
|---|---|---|---|
| B | 执行本计划，维护规范字段、状态更新、解析和评估 | 主体工作 | 按本地验收推进 |
| A | 确认原始细类、search_tags、来源和未知类型的含义 | P3 类型过滤证据 | 不重做数据；无法确定的类型保留未知，不放宽为通过 |
| C | 提供当前接口、实际返回、检查范围与失败/未知样例 | P6 接证据与失效 | 先以替身完成契约测试，运行时保持未知/未支持 |
| D | 对齐候选确认、需求展示、版本与采用后的计算入口 | P2/P4 前端整合 | B 准备可审查实现与行为测试，不暗改整体界面结构 |

这些是后续对接清单，本轮没有向组员发送消息。

## 17. 完成交付物

- F9 provider/local 契约、字段字典及更新后的 ADR/glossary。
- P1–P6 的实现、对应回归和前端流程测试。
- 保留原始数据的人工复核导入与 F9 修订候选、差异清单和复核状态。
- 旧输出离线重放报告、实现验收报告与六条浏览器流程记录。
- 已冻结但尚未调用的新测试集；获得新调用授权后才增加在线模型报告。
- B/A/C/D 联调说明，明确已支持、未接入、未知数据和未测功能。

## 18. 2026-10-28 实施记录

### 已完成的本地实现

- P0–P5 的本地契约、分段/命令校验、约束账本、细类检索、候选确认、前端状态展示和时间处理已接入。字段用途与能力边界见 `app/stage2c/requirement_fields.py`。
- P6 只把完整路线计算返回的步行/总时长检查写入短期内存证据；证据绑定 session、revision、Draft 快照和当前目录 build ID。营业时间、入口、无障碍和逐活动时段没有可用证明时仍为未核验/不支持。
- 路线手动重算与对话采用共用同一核验入口；状态变化、行程变更、会话清理、证据过期或目录重建都会使旧证据失效。
- 手动时间输入按实际时间点比较，页面支持保留非零秒。`stay_minutes` 只接受明确指向一个现有站点的 scope，防止用一个 `set_stay` 命令声称全局时长要求已满足。
- F7 历史校验保留原模式；新解析使用 F9 严格入口。

### 本地验收结果

| 验收项 | 结果 |
|---|---:|
| Python 全量测试 | 170 passed |
| 浏览器交互逻辑 Node 测试 | 23 passed |
| F7 fixture 合约检查 | 60 cases / 71 turns，0 model calls |
| F9 修订 fixture 合约检查 | 24 cases / 28 turns，0 model calls |
| 人工复核导入 dry-run | 25 个确认话轮，46 个未确认话轮；未自动晋升 gold |
| `git diff --check` | 通过；Git 仅报告 LF/CRLF 转换提示 |

### 尚未完成的验收

- F9 的 28 轮结构化 gold 仍是开发候选，不能当独立人审测试集；独立 holdout 目前只有覆盖设计，没有冻结答案。
- 本地替身测试没有验证 Gemini 真实输出，也没有完成浏览器里 B/C/D 联调。F7 原有 71 次模型调用额度已用完；再次实测需要新的明确调用上限。
- 本轮未部署。I/O、API 和验收边界见 [Person B I/O 契约](PERSON_B_IO_CONTRACT.md)、[API handoff](PERSON_B_API_HANDOFF.md) 和 [最终验收记录](PERSON_B_FINAL_ACCEPTANCE.md)。

影响：B 的本地接口可交接，解析质量、真实地图服务和浏览器联调仍不能据此判定通过。下一步需要先冻结独立人审答案；之后如需新的真实模型评估，再单独确定调用上限。
