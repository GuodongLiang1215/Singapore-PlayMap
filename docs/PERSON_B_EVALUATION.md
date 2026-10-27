# Person B 解析与约束记忆评估

本实验包含 50 条单轮和 10 组多轮，共 71 个话轮。它测试用户话语到结构化解释、命令和约束账本的过程。候选相关性、OneMap 路线可行性和完整前端采用流程需要另做端到端实验。

所有标注目前都是 `synthetic_unreviewed`，不能用来宣称最终验收通过。它们是独立写出的开发预期，没有读取模型预测后调整答案。正式实验前需要组员复核，最好另留一套不参与提示词修改的测试集。

## 运行方法

在仓库根目录用项目的 Python 环境运行：

```powershell
.venv/Scripts/python.exe scripts/evaluate_person_b.py --check-fixtures
```

这一步不调用模型。只有下面的命令会读取本地 Gemini 配置并发出可能计费的请求：

```powershell
.venv/Scripts/python.exe scripts/collect_person_b_predictions.py --max-calls 71 --output reports/person_b_predictions.jsonl
```

每个话轮只调用解释模型一次，不调用候选选择模型或 OneMap，不重试。调用上限包含失败请求。密钥、权限、模型不存在、请求格式错误或限流会提前结束。每完成一次调用就保存一行，中断后已有结果仍在。已存在的输出文件不会被覆盖。

```powershell
.venv/Scripts/python.exe scripts/evaluate_person_b.py --predictions reports/person_b_predictions.jsonl --output reports/person_b_scores.json
```

只想检查某一个文件时，可以重复传 `--fixtures`。预测必须使用相同 case ID 和从 1 开始的 turn index；缺测计入失败，重复或陌生 ID 直接报错。`reports/` 已被 Git 忽略，不提交原始模型输出。

## 指标的含义

| 指标 | 计算与限制 |
|---|---|
| `schema_pass_rate` | 通过 provider 必填字段及本地解析检查的话轮数 / 全部预期话轮数。缺测和服务商错误不算通过。 |
| `segment_micro_f1` | 对 type、name、value、scope、hardness、source 六项完全一致的语义单元计数；不要求 quote 截取边界或状态字段完全一致。 |
| `hard_constraint_recall` | 标注明确的 hard 约束有多少被原样保留。即使命令有效，漏掉语义约束仍算漏检。 |
| `command_exact_rate` | 解析成同一命令表示后比较操作、顺序、目标、数值和偏好修改；忽略 quote 和 acknowledgement。文本 notes 仍严格比较。 |
| `segment_command_consistency_rate` | 已通过结构检查的预测中，有多少通过语义一致性和引用证据校验。必须结合 hard recall 看，空 segments 不代表约束齐全。 |
| `target_sequence_accuracy` | 以标注中存在目标站点操作的话轮为分母，比较目标 ID 及操作顺序；漏掉目标操作不能退出分母。 |
| `ledger_exact_rate` | 有账本标注的话轮中，name、value、稳定 scope、hardness、status 全部一致的比例。格式错误和缺测也算失败。 |
| `clarification_decision_accuracy` | 是否需要提问的二元判断，不比较提问原句，也不证明提问内容足够好。 |
| `unproven_opening_status_claims` | 模型直接把营业约束标为 satisfied 的次数；不覆盖自然语言里的所有错误承诺，后者需要人工复核。 |

F1 使用严格规范值，不做词义相似度猜测。不同合理分段、同义名称或 quote 范围可能产生差异，需要保留失败样例供人工判断，不能只读总分。

多轮实验使用预先定义的 Draft 快照，并让模型自己的 Preferences 结果流向下一轮。发生解析或一致性错误时维持旧记忆。这是受控状态实验，不是浏览器点击 Adopt 的替代品；冲突话轮的账本重放也不表示允许冲突行程被采用。

## 可追溯字段

原始预测每行使用 `case_id` 和 `turn_index` 对齐标注；`prompt_version`、`wire_version`、`prompt_sha256`、`fixtures_sha256` 记录实际输入版本，避免提示词或样例改动后混淆实验；`model` 记录选择的模型；`metadata` 保留服务商返回的耗时、模型版本及 token 用量，便于核对调用次数。失败时只记录安全错误码，不记录密钥或异常全文。

评分报告的 `prompt_version` / `wire_version` 来自已记录的预测；`local_scoring_contract` 是执行离线校验的代码版本。因此“F7 输出在 F8 本地检查器中重放”不会被标成 F8 模型实测。首次实验限流后只继续未发送的样例，后半段的 `collection_subset_sha256` 标识收集子集，`fixtures_sha256` 仍指向完整基准。

报告只输出样例 ID、计数、判断和汇总指标，不输出用户原话或地点坐标。当前样例全为合成数据；未来使用真实数据必须另行处理授权和脱敏。

## 当前开发版本的修正

- 相同 quote 不能掩盖错误地点类别或错误目标站点。
- segments 的 quote 和命令的 quote 都必须来自当前消息或 pending 消息。
- 取消步行约束必须同时清除命令参数和账本；字段省略保持原值。
- 同轮 soft 备选不能替换 hard 要求，也不自动构成 hard 冲突。
- unresolved、unsupported 保持原状态；模型不能把营业状态升级为已满足。
- 冲突仍在时禁止 Adopt；preserve 和操作矛盾时拒绝整份解释。
- 约束在本地转成 `visit_id:ID`，新增地点预留 ID，调序后记忆跟着地点走；删除地点清理其局部约束。
- takeaway 使用对应站点的结构化意图；“不要外带”不会因出现“外带”二字而被判为外带。

这些行为由离线回归测试检查。最终验收仍需对照工作计划中的阈值，并人工检查营业承诺、澄清内容和端到端行为。

F8 增加了 `preserve_location` 名称：它只保护地点，允许修改停留时间；`preserve_visit` 仍保护整站。provider 必须输出全部八个语义字段，不能输出 satisfied 或把自己的语义判断标为 catalogue/route 来源；本地模型保留这些状态，以便未来接收确定性证据。已支持的 departure_at、finish_by、transport_mode、stay_minutes、finish_policy 使用明确字段对照校验，不再一律要求澄清。

空 quote 仍由本地校验拒绝。Google 当前说明字符串长度约束不会由解码器强制执行，所以不能声称添加 `minLength` 就解决了空引用问题；见 [Gemini structured outputs 官方说明](https://ai.google.dev/gemini-api/docs/structured-output)。F8 schema 尚未发往服务商验证。
