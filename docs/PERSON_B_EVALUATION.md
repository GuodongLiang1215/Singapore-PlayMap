# Person B 解析与约束记忆评估

F7 原开发集包含 50 条单轮和 10 组多轮，共 71 个话轮。F9 另存了 21 条单轮、3 组多轮，共 28 轮修订候选，其中关联了 25 条已确认的文字复核，另 3 轮只是多轮上下文。它们测试结构化解释和本地状态更新；候选相关性、OneMap 路线可行性和浏览器确认流程另测。

所有精确结构化答案仍是 `synthetic_unreviewed`。F9 修订使用了已经看过 F7 预测的人工意见，属于开发数据；25 条文字复核不等于逐字段确认未来的 F9 JSON。原 46 条未确认话轮不自动转为人工标注，m01 第三轮复核也不自动确认前两轮。

## 运行方法

在仓库根目录用项目的 Python 环境运行：

```powershell
.venv/Scripts/python.exe scripts/evaluate_person_b.py --check-fixtures
```

这一步不调用模型。检查 F9 修订候选时明确给出两个新文件：

```powershell
.venv/Scripts/python.exe scripts/evaluate_person_b.py --check-fixtures --fixtures tests/fixtures/person_b_f9_semantic_cases.jsonl --fixtures tests/fixtures/person_b_f9_multiturn_cases.jsonl
```

`collect_person_b_predictions.py` 的默认数据改为 F9 候选，必须显式提供 `--max-calls` 和一个新 `--output` 文件。当前没有新增调用授权，本轮没有运行收集器。以后经授权收集时，每轮只调用解释模型一次；候选选择和确认使用受控本地 fixture，不调用第二个模型或 OneMap，不重试。失败请求也计入上限，完成结果逐行落盘，已有输出不覆盖。

```powershell
.venv/Scripts/python.exe scripts/evaluate_person_b.py --predictions reports/person_b_predictions.jsonl --output reports/person_b_scores.json
```

可以重复传 `--fixtures`。预测必须使用相同 case ID 和从 1 开始的 turn index；缺测计入失败，重复或陌生 ID 直接报错。`--mode` 可以显式指定 `f7_snapshot`、`f9_snapshot` 或 `f9_confirmed_state`；省略时读取每个 fixture 的模式，旧文件默认 `f7_snapshot`。`reports/` 已被 Git 忽略，不提交原始模型输出。

## 指标的含义

| 指标 | 计算与限制 |
|---|---|
| `schema_pass_rate` | 通过 provider 必填字段及本地解析检查的话轮数 / 全部预期话轮数。缺测和服务商错误不算通过。 |
| `segment_micro_f1` | 历史模式保留六字段和原 operation 计数；F9 模式使用下行的内容定义。两者不能直接宣称为模型性能提升。 |
| `normalized_content_micro_f1` | 对 type、name、value、effect、scope、hardness、source 七项计数，只含 constraint/preference/preserve/conflict，排除本地派生 operation。micro F1 = 2TP/(2TP+FP+FN)。 |
| `hard_constraint_recall` | F9 只计 type=constraint、hardness=hard、effect=set；明确撤回单列。即使命令有效，漏掉语义约束仍算漏检。 |
| `command_exact_rate` | 解析成同一命令表示后比较操作、顺序、目标、数值和偏好修改；忽略 quote 和 acknowledgement。文本 notes 仍严格比较。 |
| `segment_command_consistency_rate` | 已通过结构检查的预测中，有多少通过语义一致性和引用证据校验。必须结合 hard recall 看，空 segments 不代表约束齐全。 |
| `target_sequence_accuracy` | 以标注中存在目标站点操作的话轮为分母，比较目标 ID 及操作顺序；漏掉目标操作不能退出分母。 |
| `ledger_exact_rate` | 有账本标注的话轮中，name、value、稳定 scope、hardness、status 全部一致的比例。格式错误和缺测也算失败。 |
| `kind_ledger_exact_rate` | 在上行基础上加入 kind，不能把 preference 当成同名 constraint。 |
| `clear_effect_accuracy` | 金标有明确 clear 的话轮中，撤回内容集合完全一致的比例；clear 与 set false 分开。 |
| `takeaway_add/replace/clear_accuracy` | 分别比较外带语义及命令；`metric_coverage` 显示分母，未覆盖时为 null，不算通过。 |
| `confirmation_success_rate` | F9 fixture 的 confirm 事件中，共享本地组装器实际完成确认的比例；格式错误和缺测仍在分母。 |
| `adopted_draft_exact_rate` | 存在 gold_adopted_draft 的话轮中比较实际采用状态；金标只用于比较，不作为状态输入。 |
| `clarification_decision_accuracy` | 是否需要提问的二元判断，不比较提问原句，也不证明提问内容足够好。 |
| `unproven_opening_status_claims` | 原输出把营业约束标为 satisfied 的次数，即使严格解析已拒绝该输出也计数；自然语言错误承诺需另行人工复核。 |

F1 使用严格规范值。仅确定性等价：单新增的 visit:new 与 visit:new:1，以及同一带时区时间所指的瞬间。park 不等于 nature，family 主题不等于已证实的儿童适宜性。quote 边界与本地状态不计入内容 F1，但仍接受格式、引用和权限检查。

`f7_snapshot` 保留历史固定 Draft 和 F8 约束账本更新规则，以便重放原实验；本地安全检查仍注明当前代码版本。`f9_snapshot` 只测当前严格解析，不自动合并偏好，不能称应用记忆准确率。`f9_confirmed_state` 从初始状态重放：continue_pending 保留待确认解释，cancel 清 Pending，confirm 使用 `assemble(..., strict=True)` 同时推进 Draft 和 Preferences。下一轮看到的是真实确认结果。固定 slots、目录和 choices 在预测前定义，不采用金标 Draft 补救错误预测；确认失败保留实际状态。

## 可追溯字段

原始预测每行使用 `case_id` 和 `turn_index` 对齐标注；`prompt_version`、`wire_version`、`prompt_sha256`、`fixtures_sha256` 记录实际输入版本，避免提示词或样例改动后混淆实验；`model` 记录选择的模型；`metadata` 保留服务商返回的耗时、模型版本及 token 用量，便于核对调用次数。失败时只记录安全错误码，不记录密钥或异常全文。

评分报告的 prompt/wire 来自已记录预测；local_scoring_contract、scorer_version、scorer_sha256、code_head、working_tree_dirty 说明本地执行版本，fixtures_sha256/predictions_sha256 说明输入。ledger_policies 另外标出历史账本策略。F7 原输出在 F9 严格入口因缺 effect 被拒绝，是契约兼容性结果，不能称为 F9 模型实测或性能退步。原报告和原 fixture 不改写。

## 复核导入和独立测试集

默认只检查并显示 25 个已确认 ID，不写文件：

```powershell
.venv/Scripts/python.exe scripts/import_person_b_review.py
```

导入先筛 CSV 的非空 decision，再比对 JSON；检查原 fixture/prediction 哈希、F8 基线提交、重复/陌生 ID、决定、确认时间、原 gold 和嵌入预测。`--output reports/person_b_review_import_new.json` 才写一个新候选清单，已有文件拒绝覆盖。文本 revise/discuss 没有完整 JSON 时标为 needs_structured_review；完整提案仍须检查，不自动晋升 human_reviewed。

已提交的 F9 manifest 还包含依据已认可答复起草的 candidate_gold；它与原答案并列，未冒充用户逐字段审阅。F9 fixture 的 28 轮只是修订候选，不是新的独立测试集。`person_b_f9_holdout_design.json` 目前只固定覆盖设计：五个尺度各 10 条单轮，标准语言 20 中文/20 英文/5 混合，另 5 个边界样例，10 组多轮共 21 轮。题目、逐字段答案和事件须另行完成人审后冻结哈希；模型调用另获授权。若以后用其预测调提示词，数据集应改列开发集。

报告只输出样例 ID、计数、判断和汇总指标，不输出用户原话或地点坐标。当前样例全为合成数据；未来使用真实数据必须另行处理授权和脱敏。

## 当前开发版本的修正

- 相同 quote 不能掩盖错误地点类别或错误目标站点。
- segments 的 quote 和命令的 quote 都必须来自当前消息或 pending 消息。
- 取消步行约束必须同时清除命令参数和账本；字段省略保持原值。
- 同轮 soft 备选不能替换 hard 要求，也不自动构成 hard 冲突。
- unresolved、unsupported 保持原状态；模型不能把营业状态升级为已满足。
- 冲突仍在时禁止 Adopt；preserve 和操作矛盾时拒绝整份解释。
- `set_stay`、外带意图和逐站偏好通过统一待采用状态更新；集合成员 clear 不会误删同组其余值。
- SELECT 只接收当前槽适用的约束/偏好；未知地点细类不会绕过名称排除。
- 核验来源与用户/推断来源分开保存，证据失效不会把意图来源改写。
- evaluator 按前端语义保留 Pending 上下文，并调用严格组装器判定确认是否可通过。

## 2026-10-28 本地验收记录

- `.venv/Scripts/python.exe -m pytest -q --tb=short`：170 passed。
- `node tests/test_chat_adoption.cjs`：23 passed。
- 默认 F7 合约检查：60 cases、71 turns、0 model calls。
- 显式 F9 合约检查：24 cases、28 turns、0 model calls。
- 人工复核导入默认 dry-run：核对 25 个已确认话轮；46 个未确认话轮保持未确认；没有自动晋升结构化 gold。
- `git diff --check` 通过；仅有 Git 的 LF/CRLF 转换提示。

这些结果证明本地契约、状态更新和回归覆盖通过；它们不测 Gemini 的新输出准确率，也不替代浏览器里的 B/C/D 实际联调。F9 精确答案仍需人工复核，新增模型实测需单独确认调用上限。完整边界和复现步骤见[最终验收记录](PERSON_B_FINAL_ACCEPTANCE.md)。

F8 增加了 `preserve_location` 名称：它只保护地点，允许修改停留时间；`preserve_visit` 仍保护整站。provider 必须输出全部八个语义字段，不能输出 satisfied 或把自己的语义判断标为 catalogue/route 来源；本地模型保留这些状态，以便未来接收确定性证据。已支持的 departure_at、finish_by、transport_mode、stay_minutes、finish_policy 使用明确字段对照校验，不再一律要求澄清。

空 quote 仍由本地校验拒绝。Google 当前说明字符串长度约束不会由解码器强制执行，所以不能声称添加 `minLength` 就解决了空引用问题；见 [Gemini structured outputs 官方说明](https://ai.google.dev/gemini-api/docs/structured-output)。F8 schema 尚未发往服务商验证。
