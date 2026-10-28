# Singapore PlayMap · Stage1C核查与更新结果

生成时间：2026-09-14T01:39:21.076843+00:00
输入验证方式：`read_only_full_stage1b_sqlite_and_profile_checked`

## 数据范围

范围为全新加坡。没有地理裁剪；这是来源记录的整理，不是完整的全岛景点普查。
输入候选源记录 **877** 条；展示地点 **869** 组。
经明确审核归组 **8** 组；删除原记录 **0**。
默认浏览显示 **860** 组，**不等于当前营业或可规划地点数量**。

## 重复候选逐对处理

| 配对 | 决策 | 原因 |
|---|---|---|
| Telok Blangah Drive Blk 79 (Telok Blangah Food Centre) / Telok Blangah Drive Blk 82 (Telok Blangah Market) | 保留分开 | 不同楼号与不同市场/熟食中心记录，保留为两个地点。不能因距离近或名称相似而合并。 |
| West Coast Drive Blk 502 (Ayer Rajah Market) / West Coast Drive Blk 503 (Ayer Rajah Food Centre) | 保留分开 | 502与503为不同楼号，源记录分别指市场及熟食中心；保留独立记录。 |
| Phoenix Park / PHOENIX PARK | 保留分开 | 同名但坐标相距约10.9公里。NHB记录地址为300 Tanglin Road，不能与NParks的同名公园合并。 |
| Raffles Place / RAFFLES PLACE PK | 相关但访问粒度不同 | 历史街区/历史标识与公园管理点的访问范围不相同；记录关联，不合并。 |
| Upper Seletar Reservoir / UPPER SELETAR RESERVOIR PARK | 相关但访问粒度不同 | 水库历史地点记录与公园管理点不是完全相同的访问对象；保留各自粒度。 |
| Katong Park / KATONG PK | 相关但访问粒度不同 | 历史地点记录描述公园历史并定位到露天剧场旁，不能仅凭同名自动等同于整个公园点位。 |
| Hajjah Fatimah Mosque / Hajjah Fatimah Mosque | 同一地点：保留多个来源 | 名称一致、同为4001 Beach Road，同一清真寺的古迹与旅游介绍资料。只建立地点组，保留两个来源及各自坐标。 |
| National Museum of Singapore / National Museum of Singapore | 同一地点：保留多个来源 | 名称一致、同为93 Stamford Road，同一博物馆的古迹与旅游介绍。组级身份不证明当前参观条件。 |
| Maghain Aboth Synagogue / Maghain Aboth Synagogue | 同一地点：保留多个来源 | 名称一致、均指Waterloo Street的同一会堂；保留不同来源的地址表述、坐标与访问条件。 |
| LABRADOR NATURE RESERVE / Labrador Nature Reserve | 同一地点：保留多个来源 | 名称一致，STB地址与NParks官网沿Labrador Villa Road的自然保护区相符；距离差异与代表点并不冲突，不取坐标平均值。 |
| JLN LIMAU KASTURI OS / JLN LIMAU KASTURI PG | 保留分开 | 源记录保留OS与PG不同对象/管理标签；现有证据不足以认定为同一地点，保守分开。 |
| JLN PELATOK OS / JLN PELATOK PG | 保留分开 | 源记录保留OS与PG不同对象/管理标签；现有证据不足以认定为同一地点，保守分开。 |
| ST MICHAEL'S PG / ST MICHAEL'S FC | 保留分开 | 源记录为PG与FC不同标签；不删除可能不同的设施/管理对象。 |
| JLN DAUD INTERIM PK / JLN EUNOS INTERIM PK | 保留分开 | 道路名Daud与Eunos不同；近邻与字符串相似不足以证明身份一致。 |
| LORONG MARICAN PARK / LORONG MARZUKI PARK | 保留分开 | 道路名Marican与Marzuki不同；保留各自公园身份。 |
| HORTPARK / HortPark | 同一地点：保留多个来源 | 名称HortPark一致，STB地址33 Hyderabad Road与NParks当前官网一致。保留两种来源坐标，不把管理代表点当入口。 |
| FORT CANNING PARK / Fort Canning Park | 同一地点：保留多个来源 | 名称Fort Canning Park一致，STB边界地址与NParks官网相符；同一公园两份来源。 |
| Gardens by the Bay / Gardens by the Bay | 同一地点：保留多个来源 | 同名、同地址、同坐标，均为Gardens by the Bay官方网站的两份旅游介绍。不是两个独立游览目的地。 |
| Lasalle College of the Arts / Lasalle College of the Arts | 同一地点：保留多个来源 | 同名、同为1 McNally Street、同坐标且简介一致，保留为同一地点的两个来源。 |

只处理配置中列出的候选配对。名称不同的其他重复记录仍可能存在；不宣称已全部去重。

## 接入点语义复核

保留输入的全部 **196** 条空间关联，不扩大半径、不重新指定最近公园。

| 标签复核结果 | 关联数 |
|---|---:|
| 名称支持该候选目标 | 80 |
| 未明确目标或无法判断 | 72 |
| 名称指向另一目标，禁止自动传播 | 20 |
| 只支持更大范围上下文 | 8 |
| 地名过宽，无法唯一确认 | 16 |

这些是源名称与边界名称的比较，不是实地验证；全部 `entrance_verified=false`。
原报告中没有空间匹配的433条接入点仍保留在Stage1B原始图层；不能说它们不存在或无用。

## 本轮官网核查

### Fort Canning Park · 常规时段
每周七天：00:00—24:00。
Fort Canning Park一般园区；不适用于内部展馆、活动及其他独立设施。
核查日期：2026-09-14；建议复核日期：2026-10-14（30天为项目策略）。
- https://www.nparks.gov.sg/visit/parks/park-detail/fort-canning-park
不确认特定日期临时关闭、活动、场内商户或实时营业。

### Labrador Nature Reserve · 常规时段
每周七天：07:00—19:00。
官网区分周边Nature Park与Nature Reserve；此处只录自然保护区7am–7pm，不能套用公园其他区域的24小时。
核查日期：2026-09-14；建议复核日期：2026-10-14（30天为项目策略）。
- https://www.nparks.gov.sg/visit/parks/park-detail/labrador-nature-reserve
不确认特定日期临时关闭、活动、场内商户或实时营业。

### HortPark · 常规时段
每周七天：06:00—23:00。
HortPark一般园区；游客服务柜台时段不同，不能把此时间覆盖到柜台或内部商户。
核查日期：2026-09-14；建议复核日期：2026-10-14（30天为项目策略）。
- https://www.nparks.gov.sg/visit/parks/park-detail/hortpark
不确认特定日期临时关闭、活动、场内商户或实时营业。

### Red Dot Design Museum, Singapore
原记录为28 Maxwell Road；官方当前地址为11 Marina Boulevard。旧坐标不能用于当前博物馆导航。
核查日期：2026-09-14；来源：Red Dot Design Museum / National Heritage Board。
运营方参观页面与NHB Roots目录均列出11 Marina Boulevard。此处只确认地址变化，不创造新坐标。
- https://museum.red-dot.sg/pages/visit-the-museum
- https://www.roots.gov.sg/places/places-landing/places/museums/red-dot-design-museum
保留旧记录，默认浏览暂缓；新地址不自动继承旧坐标，新地点暂未定位。

### Singapore Art Museum
原记录指向71 Bras Basah Road；SAM官网说明当前锚点为Tanjong Pagar Distripark，不能把旧馆址作为当前展览入口。
核查日期：2026-09-14；来源：Singapore Art Museum。
官网更新明确SAM不回到原Bras Basah建筑，当前位于Tanjong Pagar Distripark；具体入场层/入口仍需定位。
- https://www.singaporeartmuseum.sg/About/Blog/Burning-Questions-about-SAMs-New-Transformation
- https://www.singaporeartmuseum.sg/Visit
保留旧记录，默认浏览暂缓；新地址不自动继承旧坐标，新地点暂未定位。

### Jurong Bird Park, Singapore: Attractions & Things to Do
Jurong Bird Park旧址已停止作为该鸟园接待游客；不能沿用旧坐标推荐参观。
核查日期：2026-09-14；来源：Mandai Wildlife Group。
Mandai官方公告记载Jurong Bird Park于2023-01-03结束运营；后继Bird Paradise是另一个地点，不继承旧址坐标。
- https://www.mandai.com/en/about-mandai/media-centre/Visitors-bid-farewell-to-Jurong-Bird-Park.html
- https://www.mandai.com/en/bird-paradise.html
保留旧记录，默认浏览暂缓；新地址不自动继承旧坐标，新地点暂未定位。

## 仍未完成

6个来源状态暂缓地点仍待确认；原报告中的2条几何问题仍保留，没有偷偷修复。
没有获取新坐标，没有真实路线调用，没有LLM接入，没有填入游玩时长或免费标记。
网上核查仅覆盖上面明确列出的字段。完整行程规划仍需路径服务、时间检查和后续数据补充。
