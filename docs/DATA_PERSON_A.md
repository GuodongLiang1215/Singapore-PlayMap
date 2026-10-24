# PlayMap 数据工作说明（Person A：Data & POI Management）

整理日期：2026-10-22 · 负责人：Person A（数据建设）

本文档说明 Person A 已完成的数据工作：数据来源、处理流程、新增字段、数据接口、对其他成员代码的改动，以及仍然存在的限制。

---

## 1. 成果概览

| 项目 | 之前 | 现在 |
|---|---|---|
| 地点总数（目录记录） | 869 | **18,681** |
| 默认显示的地点 | 860 | **18,595** |
| 数据来源 | 5 个官方数据集 | 5 个官方数据集 + Overture Maps + OpenStreetMap（营业时间） |
| 有游玩时长的地点 | 0（全部按 5 大类写死） | **18,680**（47 个核心景点用核验值，其余按 29 个细分类别） |
| 有开放时间的地点 | 3 个人工核验 + 78 条 STB 旧资料（约 2018 年） | 核心景点 42 个 + OSM 参考 748 个 |
| 有入口坐标的景点 | 0 | 47 个核心景点，共 67 个入口（64 个有坐标） |
| 地点名称 | STB 名带乱码和网页后缀，NParks 全大写缩写 | 531 个官方地点名已清理（旧名保留为别名） |
| 数据库大小 | 6.5 MB | 约 60 MB（GitHub 单文件上限 100 MB 以内） |

对应任务表：
- ① 清洗现有 POI 数据：完成主要部分（名称清理、去重、过期记录、坐标纠错）
- ② 核验 30–50 个核心景点：完成 47 个（按官网等公开资料整理，未实地核验）
- ③ 统一数据格式和来源：完成（统一地点格式，每条记录带来源和许可，出处说明写入接口）
- ④ 提供可直接调用的数据接口：完成（新增字段 + `/nearby` 附近地点查询）

---

## 2. 数据来源与许可

| 来源 | 内容 | 许可 / 出处要求 |
|---|---|---|
| data.gov.sg（STB、NParks、NHB、NEA） | 原有 869 个官方地点 | Singapore Open Data Licence |
| Overture Maps Foundation，Places 主题，release `2026-10-21.0` | 新增 17,812 个地点（餐饮、购物、娱乐、运动、文化、自然地标） | 需注明出处，见 https://docs.overturemaps.org/attribution |
| OpenStreetMap（Overpass 下载） | 748 个地点的参考营业时间 | ODbL，需注明 "© OpenStreetMap contributors" |
| OneMap（新加坡土地管理局） | 核心景点入口坐标 | OneMap 使用条款 |
| 各景点官网 / NParks / Mandai 等 | 核心景点开放时间、收费、建议时长 | 每条记录附来源链接 |

出处说明已写入 `/api/stage1c/status` 返回的 `attribution` 字段，界面如何显示由 Person D 决定。

---

## 3. 做了什么

### 3.1 加入 Overture 地点数据

- 从 Overture 下载新加坡范围的地点，只保留：地址国家为 SG、状态为营业中、可信度 confidence ≥ 0.75。
- 类别选择：
  - 整类收录 4 个大类：`arts_and_entertainment`、`sports_and_recreation`、`cultural_and_historic`、`geographic_entities`
  - 餐饮和购物只收录 52 个细类（本地菜、熟食中心、咖啡馆、甜品、酒吧、普通餐厅、中餐、日料、快餐、奶茶、商场、百货、礼品店、书店、市集等）
- 过滤掉 202 个不适合推荐的地点：投注站 162、彩票店 25、算命 11、成人娱乐场所 4。
- 和官方数据重复的 258 条不加入（相距 150 米内且名称相似），清单保存在目录文件夹的 `overture_possible_duplicates.json`。
- Overture 的细分类别映射到 app 现有的 5 个类别：餐饮 → `food`，购物 / 娱乐 / 运动 → `tourism`，文化古迹 → `heritage`，公园和自然 → `nature`。
- 每个地点加上中英文搜索词（如 "咖啡"、"商场"、"cafe"、"shopping mall"），中英文搜索都能用。

结果：加入 17,812 个地点（餐饮 10,555、景点 / 购物 / 娱乐 4,007、历史文化 2,600、自然 650）。

### 3.2 数据清洗

- **名称清理（531 个）**
  - STB：去掉乱码和网页标题后缀，例如 `Universal Studios Singaporeâ„¢ Theme Park` → `Universal Studios Singapore`，`Merlion Park, Singapore: Attractions & Things to Do` → `Merlion Park`。
  - NParks：展开缩写并改为正常大小写，例如 `MACRITCHIE RESERVOIR PK` → `MacRitchie Reservoir Park`，`ALMOND CRESCENT PG` → `Almond Crescent Playground`。
  - 原名称保留为别名，用旧名称也能搜到。
- **去重**
  - Overture 内部重复 59 条：同名且相距 50 米以内，保留可信度最高的一条。连锁店的不同分店不受影响。
  - Overture 中与核心景点重复的 15 条：例如 Overture 自己的 "Fort Siloso"、"ACM (Asian Civilisations Museum)"。
- **过期或错误记录（隐藏，不删除）**
  - Overture 中 Jurong Bird Park 旧址（已搬迁为 Bird Paradise）
  - STB 中 River Safari（已改名 River Wonders）
  - Overture 中坐标在市中心的 "Universal Studios Singapore"（离圣淘沙约 4.5 km，疑为售票点或坐标错误）
- **坐标纠错**：NHB 数据中 Fort Siloso 的坐标在本岛，离圣淘沙实际位置约 3.5 km，改用 OneMap 坐标。
- **改名**：S.E.A. Aquarium 已于 2025 年由 Singapore Oceanarium 取代，显示名称已更新。

所有隐藏的记录都保留在数据库中，并记录了隐藏原因（`hidden_reason`），可以追溯。

### 3.3 核心景点核验（47 个）

- 核验表：`review/core_attractions_review.xlsx`，包含 46 个核心景点和可选的 Jewel。
- 每个景点整理了：开放时间、最后入场、休息日、收费、建议游玩时长（短 / 一般 / 长）、主要入口、来源链接和核验日期。
- 开放时间：42 个有。其中 25 个来自官网（含 9 月已核验的 Fort Canning Park 和 HortPark），9 个参考了 STB 约 2018 年的旧资料（官网无法读取，例如国家博物馆、佛牙寺、环球影城开园时间），8 个为无门票公共区域，按"全天开放"填写。
- 游玩时长：47 个都有。其中 4 个使用官方建议时长（万礼 4 个园区），43 个为估计值。
- 入口：67 个，其中 64 个有坐标（61 个由 OneMap 自动查询后人工检查，3 个来自 Overture）。大多为建筑位置，不是精确门口。
- 核验状态填写为"资料核对（Claude）"，表示按网上资料整理，**未实地核验**。

### 3.4 游玩时长：三层方案

1. **核心景点（47 个）**：使用核验表里的时长，优先级最高。
2. **其他地点**：按细分类别对照表 `config/duration_defaults.csv` 取默认值。共 29 条规则，覆盖全部地点，例如：主题乐园 180 / 300 / 480、博物馆 45 / 90 / 150、咖啡馆 30 / 45 / 60、甜品 15 / 20 / 30（分钟）。
3. **对照表没覆盖的**：退回原来按 5 大类写死的默认值（目前只有 1 条隐藏记录）。

对照表另有"外带"一列（咖啡馆 10、甜品 / 奶茶 5、快餐 / 小贩 10、小店 5 分钟），用于"顺路买东西"的场景。

### 3.5 营业时间补充（OpenStreetMap）

- OSM 上新加坡共有 4,742 个带营业时间的地点。按"名称相同或相似、距离 60 米内（商场 200 米）"配对，配上 748 个。
- 主要覆盖：餐厅 187、咖啡馆 168、小贩 / 熟食中心 105、商场 67、运动 48、甜品 41。
- 显示为原格式加标注，例如 `Mo-Su 10:00-22:00（OSM 参考）`。
- 已排除 OSM 中 `off` / `closed` 之类的值（例如施工中的公园）。

### 3.6 数据接口

在 `app/stage1c/api.py`（Person A 负责的地点数据接口）中：
- `/features` 和 `/entity` 返回新增字段（见第 5 节）。
- 新增 `/nearby` 附近地点查询（见第 6 节）。
- `/features` 无类别筛选时改为在数据库里分页，地点变多后加载更快。
- `/status` 返回 `attribution` 出处说明、Overture / OSM 版本信息和各步骤统计。

---

## 4. 数据处理流程（如何重建）

所有步骤都在项目根目录运行，使用项目自带的 Python：

```powershell
# 1. 下载 Overture 地点（需要网络，约几分钟）
.\.venv\Scripts\python.exe scripts\fetch_overture_places.py export --include <大类...> --include-sub <细类...>
#    查看可选类别：fetch_overture_places.py profile / profile --top food_and_drink shopping --out overture_sub.txt

# 2. 下载 OSM 营业时间（需要网络，1–3 分钟）
.\.venv\Scripts\python.exe scripts\osm_opening_hours.py fetch
#    只看覆盖率、不改数据：osm_opening_hours.py report

# 3. 核验表 → config/core_attractions.json（改完核验表后运行）
.\.venv\Scripts\python.exe scripts\import_core_review.py

# 4. 构建目录（每次数据或规则变更后运行；会生成新的目录文件夹并切换 app 使用它）
.\.venv\Scripts\python.exe scripts\add_overture_to_catalogue.py
#    撤回到官方原始目录：add_overture_to_catalogue.py --rollback
```

第 4 步依次执行：读取官方目录（只读）→ 加入 Overture → 清理官方名称 → Overture 去重 → 设置细分类别时长 → 应用核心景点核验 → 补充 OSM 营业时间 → 写入新目录并切换。每次都从官方目录重新开始，不会重复叠加。

辅助脚本：
- `scripts/geocode_entrances.py`：用 OneMap 批量查核验表中入口的坐标（需要 `.env` 中的 `ONEMAP_EMAIL` / `ONEMAP_PASSWORD`）
- `scripts/onemap_check.py`：诊断 OneMap 登录和搜索是否正常
- `scripts/clean_names.py`、`scripts/duration_defaults.py`：名称清理规则、时长对照表读取（被第 4 步调用）

注意：**不要**把 `overture_places` 加进 `config/sources.json`，Stage1C 会检查这个列表是否和 Stage1B 一致。

---

## 5. 新增数据字段（地点 entity）

| 字段 | 说明 | 示例 |
|---|---|---|
| `suggested_duration` | 建议游玩时长（分钟） | `{"short":120,"regular":150,"long":240,"basis":"official"}` |
| `suggested_duration.basis` | 时长依据：`official` 官方 / `estimate` 估计 / `category_default` 细分类别默认 | |
| `suggested_duration.rule` | 使用的对照表规则（仅类别默认） | `"cafe"` |
| `suggested_duration.takeaway` | 外带时长（只有部分类别有） | `10` |
| `opening_hours_display` | 开放时间文字（核心景点核验值，或 OSM 参考值） | `"每日 8:30–18:00"` / `"Mo-Su 10:00-22:00（OSM 参考）"` |
| `opening_hours_source` | OSM 来源时为 `osm_reference` | |
| `last_admission`, `closed_note`, `price_note` | 最后入场、休息日、收费（核心景点） | `"17:00"`、`"无"`、`"收费"` |
| `entrance_points` | 入口列表（核心景点） | `[{"name":"Tanglin Gate","latitude":1.3058,"longitude":103.8158,"type":"主入口","source":"OneMap：…"}]` |
| `core_review` | 核验信息：优先级、状态、日期、来源链接、备注 | |
| `data_source`, `overture` | Overture 来源标记和类别、可信度、品牌 | `"overture"`、`{"category":"cafe","confidence":0.95}` |
| `hidden_reason`, `duplicate_of` | 被隐藏的原因、重复的对象 | |
| `source_title`, `aliases` | 清理前的原名称（保留用于搜索） | |
| `coordinate_kind = reviewed_correction` | 坐标经过人工纠正（Fort Siloso） | |

---

## 6. 数据接口

| 接口 | 用途 |
|---|---|
| `GET /api/stage1c/status` | 当前目录版本、统计、`attribution` 出处说明 |
| `GET /api/stage1c/features?q=&category=&view=explore&limit=&offset=` | 地点列表 / 关键词搜索（GeoJSON），属性中含 `suggested_duration`、`opening_hours_display` |
| `GET /api/stage1c/entity?entity_id=` | 单个地点完整信息（含入口、核验信息、来源记录） |
| `GET /api/stage1c/resolve?uid=` | 来源记录 ID → 地点 ID |
| `GET /api/stage1c/nearby?lat=&lon=&radius_m=&category=&q=&limit=` | **附近地点**：半径内可显示的地点，按直线距离从近到远 |

`/nearby` 参数：`lat`、`lon` 必填（新加坡范围内）；`radius_m` 默认 1000，最大 5000；`category` 可选 `food` / `tourism` / `nature` / `heritage` / `monument`；`q` 关键词；`limit` 默认 20，最大 100。
返回每个地点的 `distance_m`、名称、类别、坐标、`suggested_duration`、`opening_hours_display`、`entrance_count`。
距离是**直线距离**，不是步行路线距离。推荐逻辑、步行时间计算和行程结合属于 Person C，可以在这个接口基础上做，也可以不用。

---

## 7. 对其他成员代码的改动（请对应成员检查）

Person A 的文件：`scripts/*`、`config/core_attractions.json`、`config/duration_defaults.csv`、`review/*`、`data/processed/stage1c/<新目录>`、`app/stage1c/api.py`。

以下改动在其他成员负责的范围内，合并前请检查：

**Person B（LLM & 对话）**
- `app/stage2c/prompts.py`：类别说明加入餐厅、咖啡馆、甜品、酒吧、商场、娱乐。
- `app/stage2c/catalogue.py`：Overture 来源的地点标注"Overture 第三方合并数据（非官方）"；餐饮提示文字更新；`shopping` 关键词加"百货"；同名的隐藏记录不再挡住可显示地点的搜索。
- `app/stage2c/engine.py`：用户原话中有 "to go"、"takeaway"、"grab a"、"buy a"、"外带"、"打包"、"买杯"、"顺路买" 等词时，新加入的一站设为"外带"档位（不改 LLM 输出格式和提示词）。

**Person C（GIS & 空间规划）**
- `app/stage2b/models.py`：停留档位新增 `takeaway`。
- `app/stage2b/planning.py`：有 `suggested_duration` 的地点使用地点自己的时长（核验值或细分类别默认值）；外带档位使用外带时长；其余仍按原来的 5 大类默认值。
- 新增 `/nearby` 接口可供附近推荐使用；入口坐标在 `entrance_points`，可用于"路线到入口"。

**Person D（系统集成 & UI）**
- `frontend/stage1c|stage2a|stage2b|stage2c` 的类别名："熟食中心" → "餐饮"，"景点资料" → "景点、购物与娱乐"。
- `frontend/stage2b/mapview.js`：地图地点改用 canvas 绘制（1.8 万个点时更流畅）。
- `frontend/stage2b/app.js`：时长预览显示地点自己的时长；地点卡片显示开放时间；档位下拉框新增"外带"。
- `frontend/stage2b/state.js`：允许"外带"档位。
- `frontend/stage1c/map.js`：Overture 来源记录的链接显示为 "Overture 数据说明"。
- 部署：新目录文件夹约 60 MB，需要随代码一起提交；`/status` 中的 `attribution` 可用于在界面显示数据出处。

---

## 8. Limitations（汇报时说明）

1. **游玩时长多为估计值**：47 个核心景点中 43 个为估计值，只有万礼 4 个园区使用官方建议时长；其他地点使用细分类别默认值，均为团队估计，不是实测数据。
2. **开放时间未实地核验**：核心景点按官网等公开资料整理；其中 9 个参考了约 2018 年的 STB 旧资料；5 个核心景点（Singapore Oceanarium、缆车、MacRitchie、乌敏岛、Jewel）缺少开放时间。
3. **营业时间覆盖率低（约 4%）**：免费且可合法存储的营业时间数据很少。Overture 和 OneMap 没有营业时间，OSM 只有 4,742 个新加坡地点有，配上 748 个，且为社区编辑、可能过时。Google 等商业数据要付费，且条款不允许批量存储。
4. **入口坐标**：大多为 OneMap 的建筑位置，不是精确门口；3 个入口（MBS Tower 3、Nassim Gate、Marine Cove）缺坐标。路线目前仍计算到景点代表点。
5. **Overture 为第三方合并数据**：可能有已关门的店或坐标偏差；只按可信度 ≥ 0.75 和"营业中"过滤。
6. **残留重复**：名称差别大的重复（例如 "Lau Pa Sat" 与 "Former Telok Ayer Market"）、同名但相距 50 米以上的记录没有处理；258 条"可能与官方重复"的 Overture 记录按规则未加入，没有逐条人工复核。
7. **类别映射较粗**：Overture 细分类别映射到 app 现有的 5 个类别，例如商场、运动场馆都归在 `tourism`。

---

## 9. 待办

**Person A（手动补充，补完后重跑第 4 节的第 3、4 步）**
- 3 个入口坐标：MBS Tower 3、Nassim Gate、Marine Cove（Google Maps 右键取坐标）
- 5 个核心景点的开放时间：Singapore Oceanarium、缆车、MacRitchie、乌敏岛、Jewel

**提交前**
- 删除旧的目录文件夹，只保留 `data/processed/stage1c_current.json` 指向的最新目录和原始目录 `20260914T013921075700Z`
- 删除临时文件：`.env.bak`、`overture_profile.txt`、`overture_sub.txt`、`review/core_attractions_review.backup.xlsx`
- 确认 `.env` 没有被提交

**未来改进（建议）**
- 商场按"一般营业时间 10:00–22:00（估计）"补充
- 用户点开地点卡片时实时查询营业时间（例如 Google Places，需付费并在界面注明出处，不存储）
- 外带地点优先推荐在路线附近的店，并按途经点计算绕路时间
