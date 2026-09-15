# Singapore PlayMap — 小组共享部署

目标：组员只打开一个 HTTPS 网址即可使用当前 PlayMap。Gemini API Key 与 OneMap 账号凭据只保存在 Render 服务端环境变量中，不进入 GitHub 或浏览器。

## 当前部署设计

- 单个 FastAPI / Uvicorn worker（当前聊天草案和路线缓存是进程内状态，不能开启多个 worker）。
- 每个浏览器生成独立的 32 位会话 ID；路线缓存与待确认草案按 session 隔离。
- 同一浏览器同一时刻只允许一个 Gemini 请求；共享实例默认最多 2 个并发聊天会话。
- 全局 Gemini 请求由进程内节流器控制，默认间隔 2.0 秒，可用 `PLAYMAP_LLM_MIN_INTERVAL_S` 调整（0–60，异常值回落到 2.0）。这是本项目自定的保守下限，不是实测的服务商额度；被限流时返回 429 并提示稍后重试，不会自动重发。
- OneMap Token 只缓存于服务器内存；缺失、临近过期或被拒绝时，使用服务端 `ONEMAP_EMAIL` / `ONEMAP_PASSWORD` 最多重新认证一次。
- 共享网址默认要求 `PLAYMAP_ACCESS_CODE`。组员首次输入访问码后获得 HttpOnly cookie，API Key 不会暴露到浏览器。

## 1. 先在本机生成干净的云端仓库目录

在当前项目根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_render_repo.py
```

脚本会在 `D:\`（项目父目录）生成类似：

```text
D:\Singapore_PlayMap_RenderRepo_20260914T....Z
```

只包含：程序代码、前端、配置、Stage1C 全国地点目录快照、Render 配置和本说明。

不会包含：`.env`、Gemini Key、OneMap Token/密码、`.venv`、原始下载、聊天内容、诊断报告。

## 2. 之后再上传到私有 GitHub 仓库

这一步等代码和本机验证完成后再做。不要把原项目根目录直接 `git add -A`。

## 3. Render 需要设置的 Secret 环境变量

`render.yaml` 会声明变量名称，但不会保存秘密值。创建服务时在 Render Dashboard 填写：

```text
PLAYMAP_ACCESS_CODE=<给组员的一段随机访问码，至少8位>
GEMINI_API_KEY=<你的Gemini key>
ONEMAP_EMAIL=<你的OneMap注册邮箱>
ONEMAP_PASSWORD=<你的OneMap密码>
```

`PLAYMAP_SESSION_SECRET` 由 Render 自动生成。

非秘密配置已经写入 Blueprint：

```text
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
PLAYMAP_MAX_LLM_CONCURRENCY=2
PLAYMAP_LLM_MIN_INTERVAL_S=2.0
PLAYMAP_DEPLOYMENT=render
```

## 4. Render 服务

Render 使用：

```text
Build: pip install -r requirements-cloud.txt
Start: uvicorn app.stage2c_main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips='*'
Health: /health
```

部署成功后，访问 Render 分配的 HTTPS 地址。未登录时会跳转 `/team-login`。

## 5. 组员如何用

组员只需要：

1. 打开网址；
2. 输入小组访问码；
3. 正常使用地图和聊天。

不需要 Python、VS Code、Git、Gemini Key 或 OneMap 账号。

### ⚠️ 第一次打开可能要等约 50 秒，这不是坏了

Render 免费实例在长时间无人访问后会休眠。下一次访问时，Render 会挂起请求去唤醒实例，**期间浏览器只显示空白页和转圈**。

这 50 秒里服务端还没开始响应，所以页面上无法显示任何提示——程序本身还没送到浏览器。请转告组员：

- **耐心等待，不要反复刷新**（刷新会重新排队，只会更慢）；
- 醒来之后所有操作都是正常速度，不会再等；
- 一段时间无人使用会再次休眠。

演示前的建议做法：**开场前几分钟先自己打开一次**把服务叫醒，组员就都是秒开。

所有人共享同一个 Gemini 项目额度与 OneMap 账户调用；这不会为每位组员复制免费额度。

## 6. 当前限制

- Render 免费实例可能休眠；首次唤醒可能较慢。
- 服务重启后，尚未确认的聊天草案和路线缓存会消失；地点目录仍在。
- 当前只部署 Stage1C 核心地点目录；旧的 Stage1A/1B 调试数据不会默认放入 GitHub。
- 当前入口与营业时间仍未全面核验；部署不会改变这些研究边界。
- 不要让组员输入私人住址或敏感信息到免费 Gemini 测试实例。
