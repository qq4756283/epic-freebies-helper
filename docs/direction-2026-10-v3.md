# 方向文档 v3：Epic 周免助手 —— 从"能跑"到"能证明领到了"

> 状态：**方向决策草案（待批）**　日期：2026-10-21　仓库：`qq4756283/epic-freebies-helper`
> 上游血缘：`QIN2DIM/epic-awesome-gamer` → `Ronchy2000/epic-freebies-helper` → 本仓库
> 证据基线：GitHub Actions 全量 138 次运行记录 + 本地 19 份运行日志（含 15 份失败、3 份成功）+ 本地源码与工作区改动
> 前序文档：`docs/refactor-plan-2026-10.md`（v2，零预算版）。本文**取代**其第 5 章架构结论，保留其 §1.5「API 硬解不可行」与 §4 验证码降级链。

---

## 0. 一页结论

**三句话判定：**

1. **项目当前是"假活"状态。** 2026-10-14 起没有一次定时触发（应有 5 次），而四个 workflow 全部 `state=active`。也就是说：即便代码完美，本周也不会领到东西，而且没人会知道。
2. **失败几乎全在登录，不在领取。** 15 份失败日志里 10 份是 `auth_failed_captcha`（8 次重试、每次等到 155s 超时、12–35 分钟全废），5 份是领取确认失败。领取状态机本身没有被打穿，是**进不去门**。
3. **三次"success"全是空转。** 三份成功日志的结局都是 `Epic Games is already logged in` → 两个游戏按钮都是 `In Library` → 直接 `outcome=claimed_ok`，耗时 1m43s–1m51s，**没有一次真正的点击领取**，而且订单历史探测在三次里都报 `JSONDecodeError`。**真实领取链路在观测窗口内没有任何一次被证明成功过。**

**推荐主线（一句话）：**
> 把重心从"解验证码"移到"不产生验证码 + 不撒谎 + 不静默"—— **令牌种子会话（免登录）+ 真值验证（只认订单/权益）+ 心跳看门狗（调度死了能被发现）**，验证码层降级为兜底，并且只在 90 秒内决断。

**立刻要做的 4 件事（P0，都不需要新设计）：**

| # | 动作 | 为什么 | 验收 |
| --- | --- | --- | --- |
| P0-1 | 提交并推送工作区已有的三项修复（两次级缓存 `ok`/`last`、`AUTH_MAX_ATTEMPTS=3`、`stage-events.jsonl` 遥测） | 这些只存在于本地未提交改动，**线上跑的仍是"失败覆盖成功 + 8 次重试"的老代码**，10-08~10-11 的 4 连败正是老代码 | 远端 workflow 里能看到 `epic-session-ok-` 与 `AUTH_MAX_ATTEMPTS: ... '3'` |
| P0-2 | 手动触发一次 `workflow_dispatch` 复跑，确认线上行为与本地修复一致 | 8 天没跑过，任何结论都过期了 | run 日志出现 `Stage event \| stage=auth`，且 `AUTH_MAX_ATTEMPTS=3` |
| P0-3 | Cherry-pick 上游 `7c3bfb5`（hsw.js identity 路由 + 挑战处理加固）与 `70354be`（checkout 帧导航守卫） | 15/15 日志都有 `NS_ERROR_INVALID_CONTENT_ENCODING`；`Cannot find a valid challenge frame` 出现 1–5 次/轮 | 新 run 的 `ns_error` 计数归零或显著下降 |
| P0-4 | 加最小 deadman：外部心跳 + `scoreboard.json` + 超 8 天无成功就告警 | 10-14 起 8 天无人知，这是比验证码更致命的失效 | 手工停跑 8 天能收到告警（用 `HEARTBEAT_FORCE_ALERT=1` 演练） |

---

## 1. 证据基线

### 1.1 上游三条线：两条已经停摆，第三条有 12 个未合入的修复

| 线 | 最新提交 | 活跃度 | 与本仓库关系 |
| --- | --- | --- | --- |
| `QIN2DIM/epic-awesome-gamer` | `43625338` 2025-11-07 | 停更近 12 个月（1130★） | 祖宗项目，无新东西可拿 |
| `Ronchy2000/epic-freebies-helper` | `d90190bf` 2026-09-06 | 停更 6 周（258★） | **直接父仓库：本仓库落后 12 个提交、领先 32 个提交** |
| `qq4756283/epic-freebies-helper` | `5c402c5e` 2026-10-02 | 19 天无提交 | 本仓库（origin 与本地 HEAD 一致） |

Ronchy 侧未合入的 12 个提交（`git diff origin/master...ronchy/master`，28 文件 +1745/−104）：

| 提交 | 主题 | 对本项目的价值 |
| --- | --- | --- |
| `7c3bfb5` | fix: harden hcaptcha challenge handling | **高**。给 `**/hsw.js*` 强制 `accept-encoding: identity`，直接治 `NS_ERROR_INVALID_CONTENT_ENCODING`；附带挑战帧就绪判定、GLM 提示词加固、`.gitignore` 与文档 |
| `70354be` | fix: guard Firefox checkout frame navigation | **高**。`playwright_frame_guard.cjs` + 跳过已在进行中的 checkout（`_has_purchase_progress`），治"重复提交把在途 iframe 打掉" |
| `8d2b740` / `a177c80` | fix: stabilize GLM multi-select captcha prompts | 中。多选题型提示词稳定化 |
| `7212892` | Merge #26 fix-animal-count-hcaptcha | 中。数字/计数类题型 |
| `750d5b8` / `bc9cc1e` | feat: WXPush 微信推送（含非致命处理） | 中。多一条国内可达的推送通道 |
| `6da5f15` / `50043c4` / `803fc8b` | issue 表单、CLAUDE.md 符号链接、维护日志 | 低 |

另有 3 个分支值得盯（未合主干）：`protocol-provider-architecture`（协议层 provider 抽象）、`add-openai-provider`、`add-deepseekv4-provider`。**都还是文档阶段**，不构成立即可用的能力。

### 1.2 CI 真实成绩（全量 138 次运行）

| 工作流 | success | failure | cancelled | 合计 |
| --- | --- | --- | --- | --- |
| Epic Awesome Gamer (Scheduled) | 36 | **58** | 13 | 107 |
| Epic Session Keepalive | 8 | 1 | — | 9 |
| Tests | 7 | 4 | — | 11 |
| Update Star History | — | — | 11（skipped） | 11 |

- 领取工作流原始成功率 **36/107 = 34%**，且含有大量"空转成功"（见 §1.5），**真实领取成功率更低**。
- 单次运行中位耗时 **7.5 分钟**，最长的 60 分钟是撞 `timeout-minutes` 被取消——说明有稳定复现的挂死路径。
- 近 30 天节奏：10-02 成功 → 10-03/04 成功（空转）→ 10-08/09/10/11 四连败 → 10-13 keepalive 成功 → **此后完全静默**。

### 1.3 关键发现：定时链路已经静默死亡

- 全部 workflow 的 `state=active`，`epic-gamer.yml` 里有 4 条 cron（周四/五/六/日 15:20 UTC），`epic-session-keepalive.yml` 有 1 条（周二 03:00 UTC）。
- 实际触发：**最后一次 schedule 事件是 2026-10-13（keepalive）**。10-15、10-16、10-17、10-18、10-20 这 5 次应触发全部缺失。
- 仓库自 2026-10-02 起无提交。GitHub 对长期无活动的仓库会自动停用 schedule，且**不会发任何通知**；高峰期的 cron 也可能被丢弃。
- 结论：**"被静默停摆"比"验证码解不出"更致命**，因为它把可观测的失败变成了不可观测的缺失。任何方案都必须自带 deadman 检测，不能假设 cron 一定会来。

### 1.4 失败分类（本地 15 份失败日志，逐条）

| run id | 日期 | 时长 | 阶段 | 主因签名 | 关键计数 |
| --- | --- | --- | --- | --- | --- |
| 36565377104 | 09-29 | 27m | AUTH | `auth_failed_captcha`（8 次） | GLM 500×10 + 502×1；ns_error×4；frame 缺失×2 |
| 36593937736 | 09-29 | 23m | AUTH | 同上 | ns_error×3；无 GLM 调用（未出题） |
| 36598106099 | 09-29 | 25m | AUTH | 同上 | GLM 500×4；ns_error×4 |
| 36601683469 | 09-29 | 30m | AUTH | 同上 | ns_error×4；`Locator.screenshot: Timeout 30000ms` |
| 36606776824 | 09-29 | 35m | AUTH | 同上 | ns_error×5；frame 缺失×3；多选×2 |
| 36612761823 | 09-29 | 27m | CLAIM | `Failed to confirm claim flow`（astrea） | tenacity `n=1 sleep=0.0` ×10 |
| 36613351338 | 09-29 | 12m | AUTH | `auth_failed_captcha` | 出现 2 次登录流程（重启浏览器） |
| 36616965478 | 09-29 | 15m | CLAIM | 同上（astrea） | tenacity ×10；order history 探测 ×8 |
| 36619233239 | 09-29 | 15m | CLAIM | 同上（astrea） | 同上 |
| 36623373531 | 09-29 | 17m | CLAIM | 同上（astrea） | 同上 |
| 36920932284 | 10-01 | 30m | CLAIM | 同上（System Shock 2 + Buried Stars） | checkout 期出题（checkout:2/3）；ns_error×23 |
| 37841379121 | 10-08 | 15m | AUTH | `auth_failed_captcha` | 8×`login_outcome_timeout`；ns_error×4 |
| 37984943178 | 10-09 | 15m | AUTH | 同上 | 同上 |
| 38079483724 | 10-10 | 29m | AUTH | 同上 | **GLM 500「no account available」×66 + 502×7**；`payload timeout`×15 |
| 38168649816 | 10-11 | 30m | AUTH | 同上 | **GLM 500×81**；`payload timeout`×21；frame 缺失×5 |

聚合：

| 失败模式 | 次数 | 占失败 | 直接原因 |
| --- | --- | --- | --- |
| 登录 hCaptcha 无法完成（8 次重试后放弃） | 10 | 67% | 模型层不可用（500/502）+ 挑战帧拿不到 + 载荷等待超时 |
| 领取确认失败（点击后无法确认订单） | 5 | 33% | 重试装饰器只跑 1 次（`n=1 sleep=0.0`）+ 订单历史探测失效 |
| 其中"模型网关硬故障" | 2/10 | — | 自建网关 `http://115.120.244.178:5242/v1/chat/completions` 明文 HTTP，返回 `no account available` / `WorkBuddy upstream returned no answer` |

三条可量化结论：

1. **一次性下线就能解释大部分历史失败**：把 38079483724 与 38168649816 的 GLM 网关故障剔除，这两轮的失败是"solvable"的——但当时的代码没有"provider 不健康就别进登录流程"的判断。
2. **每次验证码等待 155s，8 次重试≈20 分钟**（`hCaptcha challenge wait failed | timeout=155.0s`）；`AUTH_ATTEMPT_TIMEOUT_SECONDS=240` 之上再叠加重试，一次失败就把 25 分钟预算烧光。
3. **`ns_error`（hsw.js 解码失败）在 15/15 日志里都存在**，是慢性病；它对应用户仓库从未合入的上游修复 `7c3bfb5`。

### 1.5 三份"成功"日志全是空转（假成功）

```
SUCCESS services.epic_authorization_service:invoke - Epic Games is already logged in
WARNING services.epic_games_service:_load_order_history_payload - Failed to load Epic order history
        session probe: JSONDecodeError('Expecting value: line 1 column 1 (char 0)')
SUCCESS services.epic_games_service:add_promotion_to_cart - Game already claimed / unavailable
        (purchase button disabled) - title='System Shock 2 ...' text='In Library'
SUCCESS ... title='BURIED STARS' text='In Library'
INFO services.run_outcome:write_run_outcome - Run outcome recorded | outcome=claimed_ok
```

三份日志（`ok-37057555847`、`ok-37145277172`、`ok-37225282197`）是同一个形状：

- 会话有效 → 跳过登录（**0 验证码**），证明"会话复用是最高 ROI"这条判断成立；
- 订单历史探测全部失败 → `_sync_order_history()` 拿到空列表 → 代码认为"有未领取游戏"；
- 逐商品打开页面，按钮已经 `In Library` → 直接记成功；
- `outcome=claimed_ok` → workflow `success` → 写 `epic-session-ok-*` 缓存。

**后果有三层**：① 成功率指标虚高；② `claimed_ok` 这个终态不再代表"本周真的领到了东西"；③ 什么都没做的运行也能把会话档案标成"已验证"，污染下一轮的健康判断。

### 1.6 验证层已坏：订单历史探测 100% 失败

`app/services/epic_games_service.py` 的验证逻辑依赖 `page.goto("https://www.epicgames.com/account/v2/payment/ajaxGetOrderHistory")` 然后把页面文本当 JSON 解析（`_load_order_history_payload`，L257–271）。日志显示这条路径**在每一轮（含成功轮）都抛 `JSONDecodeError`**：该端点要求 `Accept: application/json` + AJAX 语义，用导航抓 `<pre>` 的做法已经不可靠。

连锁影响：

- `_sync_order_history()` 返回 False → `namespaces` 为空 → **"已拥有"判定永远失效**；
- `_has_account_session()` 恒为 False → 会话健康判断失真；
- `_confirm_promotion_claimed()` 的第三条证据（订单历史命中）永远不成立 → 09-29/10-01 那 5 次 `Failed to confirm claim flow` **本质上是确定性失败，不是偶发**；
- 领取成功与否只能看商品页按钮文案，而 `In Library` 无法区分"本周领的"和"以前买的"。

### 1.7 工作区里已有的、但线上没跑的修复

`repo/` 工作区相对 `origin/master` 有 8 个文件未提交（+1438/−1096，含 `uv.lock` 重排）：

| 改动 | 文件 | 价值 |
| --- | --- | --- |
| 会话缓存双 key（`epic-session-ok-*` / `epic-session-last-*`，成功才写 ok） | `.github/workflows/epic-gamer.yml` | **高**，治"失败覆盖成功"的雪崩 |
| `AUTH_MAX_ATTEMPTS` 8 → 3（设置侧 `le=5`） | `app/settings.py`、工作流 env | **高**，把 20 分钟失败压到 7 分钟 |
| 阶段遥测 `stage-events.jsonl`（stage/result/ms/metrics，永不抛） | `app/services/run_outcome.py`、`app/deploy.py` | **高**，第一次能按阶段复盘 |
| `mark_active_profile_verified()` 只在领取结局后写健康位 | `app/services/browser_context.py`、`app/deploy.py` | 高 |
| LLM 适配器加固（60 行） | `app/extensions/llm_adapter.py` | 中 |
| v2 重构计划、探针/评测脚本、run.ps1 | `docs/`、`scripts/` | 决策依据 |

**这些都不在线**。也就是说，最近 4 次失败用的还是老逻辑：4 条 cron 各自冷启动、失败也回存档案、8 次重试。

---

## 2. 优势与劣势盘点

### 2.1 必须保留（这些是别人没有的）

| # | 优势 | 证据/位置 | 保留方式 |
| --- | --- | --- | --- |
| S1 | **会话复用能让整轮 0 验证码** | 3 份 ok 日志：`already logged in` → 1m43s 完成 | 升级为"令牌种子会话"，把它从"运气好"变成"设计如此" |
| S2 | **本地 CV 解题器**（轮廓/连线/网格，1143 行级） | `hcaptcha_adapter.py`、`numbered_line_solver.py` | 保留为坐标类题型唯一可信来源；模型只做离散题 |
| S3 | **领取状态机**（checkout 容器选举、instant checkout、reconciliation、device-not-supported 弹窗处理） | `epic_games_service.py` L1027–1980 | 不动主干，只补"幂等 + checkpoint" |
| S4 | **阶段遥测与结局分类**（本地已写，未提交） | `run_outcome.py`、`deploy.py` | 提交并扩展成周报数据源 |
| S5 | **零货币成本**（Actions 公开仓库 + 免费模型 + 本地算力） | v2 文档 §3 | 不变 |
| S6 | **文档纪律**（维护日志、plan、复现步骤） | `docs/maintenance-log.md` 137KB | 继续 append-only |
| S7 | **多账号 + 通知通道**（Telegram、WXPush 可摘） | `accounts.py`、`telegram_notification_service.py` | 保留；通知内容要改（见 D2） |
| S8 | **失败可复现的日志/截图工件**（runtime、logs、screenshots 三件套 7 天） | `epic-gamer.yml` L277–302 | 保留，延长到 30 天供复盘 |

### 2.2 必须去掉（这些是纯负债）

| # | 劣势 | 证据 | 处置 |
| --- | --- | --- | --- |
| W1 | **登录=验证码**的默认路径（冷启动必出题） | 10/15 失败在 AUTH | D1：免登录会话；只有会话真失效才回退密码登录，且每周 ≤1 次 |
| W2 | **8 次重试 × 155s 等待** | `AUTH_MAX_ATTEMPTS=8`；`timeout=155.0s` | 已改 3；再加"单题 90s、总预算 5 分钟、到点就 `needs_human` 退出" |
| W3 | **LLM 单点依赖（明文 HTTP 自建网关）** | 500 `no account available`×66/81 | 官方 HTTPS GLM 为主 + 运行前探活 + 不健康直接跳过模型层 |
| W4 | **订单历史用 HTML 抓取** | 每轮 `JSONDecodeError` | 换成带正确 header 的 API 调用（`page.request.get` / `expect`），并加 entitlements 第二证据 |
| W5 | **假成功**（`In Library` → `claimed_ok`） | 3/3 ok 日志 | 三态结局：`claimed` / `already_owned` / `needs_human`，只有前两者算 success，且 `already_owned` 不写"已验证会话"以外的信号 |
| W6 | **4 条 cron 各自冷启动** | 4 条 cron，每次独立 restore | 收敛为"周四主跑 + 6h 后一次补跑"，降低出题率与风控分 |
| W7 | **调度静默死亡无人知** | 10-14 起 0 次触发 | 心跳看门狗 + deadman 通知（详见 D5） |
| W8 | **失败不留可判定证据**（8 次尝试只留一句 `auth_failed_captcha`） | 日志 | 每次验证码尝试落 `stage-events` + 单图 + 题型 + provider 结果 |
| W9 | **60 分钟挂死运行** | 13 次 cancelled，最长 60m | 硬预算：单账号 12 分钟强制收尾，写 partial 结局 |
| W10 | **上游修复长期不合**（12 提交挂 6 周） | §1.1 | 设"上游同步"例行任务，每两周 cherry-pick 一次 |

---

## 3. 方向候选与评估

评分口径：**成功率提升**（对"本周真的领到"的边际贡献）、**成本**（货币/工时/风控）、**可验证性**（能否在 1 小时内证伪）。

| 方向 | 一句话 | 成功率提升 | 成本 | 可验证性 | 结论 |
| --- | --- | --- | --- | --- | --- |
| **D1 令牌种子会话** | 用 device auth + 授权码交换把登录态直接灌进浏览器，绕开登录页 | ★★★★★ | 中（1–2 天） | 高（curl 30 分钟可判） | **采纳为主线** |
| **D2 真值验证 + 三态结局** | 只认订单/权益证据，`In Library` 不再等于成功 | ★★★★ | 低（半天） | 高 | **采纳** |
| **D3 幂等领取状态机** | checkpoint 化、只对缺失商品操作、单次准点触发 | ★★★☆ | 中（1–2 天） | 高 | **采纳（P1）** |
| **D4 验证码"少出快逃"** | 上游 hsw 修复 + 本地 CV 优先 + provider 健康门 + 90s 决断 | ★★★ | 低（半天） | 高 | **采纳（P0）** |
| **D5 心跳看门狗 + 双执行面** | 外部心跳触发/告警；可选家用 runner 执行 | ★★★★（防静默） | 低 | 高 | **采纳（P0，最先做）** |
| D6 人工 1-click 兜底 | 失败推送"已登录的预填链接"，用户 30 秒点完 | ★★（兜底价值高） | 极低 | 高 | 采纳为兜底 |
| D7 付费打码/住宅代理 | 2captcha、BrightData | ★（Talon 不认第三方 token） | 高（违背零预算） | 低 | **不做** |
| D8 纯 API 领取（无浏览器） | 直接调 quickPurchase/confirm-order | ☆ | 极高 | 已证伪 | **不做**（v2 §1.5 保留结论） |
| D9 换语言重写（Node/Playwright 原生） | 迁移到 `vogler/free-games-claimer` 路线 | ★★ | 极高 | 中 | 暂不做（机会成本过大） |

### D1 令牌种子会话（核心方向）

**问题**：现在唯一能跳过验证码的方式是"上次的浏览器 profile 还没过期"。profile 一过期（cookie 7–30 天、Epic 侧刷新策略不可控），就必须走登录页 → 出题 → 8 次重试 → 25 分钟 → 失败。**把"希望会话别过期"变成"每次运行前 2 秒重建会话"，才是结构性解法。**

**做法（待实测确认的两个假设，见 §6 实验 E1/E2）**：

1. **一次性**（人工，5 分钟）：用 Epic 官方 launcher 公共 client 做 device auth，拿到 `refresh_token`，存进仓库 secret `EPIC_REFRESH_TOKEN`。之后**不再需要密码**（也就没有密码登录 → 没有登录验证码）。
2. **每次运行前（纯 HTTP，<2s）**：`grant_type=refresh_token` 换 `access_token`，**并回写轮换后的 refresh_token**（Epic 会滚动）。
3. **播种浏览器**：用 access token 走 `id/api/redirect?...&responseType=code` 拿授权码，再走 `id/api/exchange?code=...` 让 `epicgames.com` 域下发登录 cookie；把 Set-Cookie 通过 `context.add_cookies()` 注入（或直接在浏览器里访问这两个 URL）。之后打开 store 就是登录态。
4. **失效才回退**：`refresh_token` 被吊销（改密码/风控/手动登出）时，走一次密码+2FA 登录，成功后重新生成 refresh_token 并写回 secret（需要 `gh secret set` 或 PAT；无 PAT 就降级为人工更新）。

**为什么它比"多跑几次"强**：登录页是验证码的唯一产地。把登录次数从"每周 4–8 次"降到"每月 0–1 次"，等于把出题概率按数量级压低，而不是把解题率提高几个百分点。

**风险**：`refresh_token` 等价于账号凭据，泄露=账号失控；必须只存在 Actions secret（不落日志、不落 artifact），本地 `.env` 用占位。另外 Epic 可能对 web 交换加设备指纹要求——这正是 E1/E2 要验证的。

### D2 真值验证 + 三态结局

**做法**：

1. 订单历史改 API 调用：`page.request.get(URL_ORDER_HISTORY, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})`，解析 `orders[]`；断言 `orderType=PURCHASE` 且 `items[].namespace` 命中。
2. 加第二证据：`entitlements`（`/account/api/.../entitlements`）或 library 接口命中 offer；两条任一成立即可判 `claimed`。
3. 商品页 `In Library` **只用于 `already_owned`**，不再单独产出 `claimed_ok`。
4. 结局三态写 `run-outcome.json`，workflow summary 与推送文案都按三态分支；`already_owned` 不再写 `epic-session-ok`（那是"会话有效"的信号，不是"领取成功"的信号——两者分开两个 key）。

**验收**：任意一轮结束后，`run-outcome.json` 里能读出 `claimed: ["BURIED STARS"]` 或 `already_owned: [...]`，并且与 Epic 网页端"订单历史"逐条对得上。

### D3 幂等领取状态机

**做法**：

- 进入领取前先做一次真值快照（D2），**只对缺失商品操作**；
- 每个商品一个 checkpoint 文件（`runtime/claim/<namespace>.json`：`state/via/order_id/at/evidence`），重跑时跳过已完成；
- "已在进行中的 checkout"用上游 `_has_purchase_progress` 守卫，禁止二次提交；
- 预算：单商品 90s、单账号 8 分钟、整轮 12 分钟；到点写 `partial`，不抛异常退出（异常退出会触发 failure 缓存路径，反而更难诊断）；
- 调度：周四主跑 1 次 + 6 小时后补跑 1 次；**不再 4 次**（Epic 有 24h 领取限制，反复重试只会抬高风控）。

### D4 验证码"少出快逃"

- **合上游 `7c3bfb5`**：`**/hsw.js*` 强制 `accept-encoding: identity`（治 ns_error），挑战帧就绪判定加固；
- **合上游 `70354be`**：frame guard，防 checkout 帧被二次导航打断；
- **provider 健康门**：进入登录/领取前一次 200ms 探活；不健康 → **不进验证码流程**，直接 `needs_human`（3 分钟内结束），而不是 8×155s；
- **单题预算**：passive 8s → 本地 CV 12s → 单模型 45s → 失败即换题型处理或放弃；**累计 90s 无进展就放弃该题**；
- **模型调用改为官方 HTTPS 端点**（`open.bigmodel.cn`，本地 `.env` 已是此配置），自建明文网关降级为可选备份；
- 保留题型路由（v2 §4.1 的实测结论仍成立）：坐标类只走本地 CV，离散类才问模型。

### D5 心跳看门狗 + 可选双执行面

- **deadman 三件套**：
  1. 每轮结束把 `{date, outcome, claimed[], duration}` append 到 `docs/scoreboard.json`；
  2. 外部心跳（`cron-job.org` 免费 / 家里 PC 的 Windows 计划任务）每 6h 调 `workflow_dispatch`，**不依赖 GitHub 自己的 cron**；同一次调用顺带检查"距上次成功是否 >8 天"，超了发告警；
  3. 告警走 WXPush/Telegram（国内可达）。
- **可选家用 runner**（P2）：家用 Windows + 住宅 IP + 持久 profile，反而比 Actions 更不容易出题（v2 §1.5 的"机房 IP 是出题主因"）。用 `repository_dispatch` 让 Actions 只做调度与通知，执行在家用机。零货币成本，但要求机器在线——所以**先做 Actions 版本，runner 作为可选增强**。

### D6 人工 1-click 兜底

失败推送里直接给"已登录商品页"链接 + 一句"点一下 Get 就完成"。这是把"失败"变成"30 秒人工"的降级路径，成本最低、永远可用。前提：推送里带的是能直接点的 URL（`https://store.epicgames.com/en-US/p/<slug>`），不是商店首页。

---

## 4. 推荐架构 v3

```
                    ┌──────────────────────────────┐
 外部心跳 ────────▶ │ L0 调度与守卫                 │
 (cron-job.org /   │  dispatch · deadman · 预算闸门 │
  家用计划任务)     └──────────────┬───────────────┘
                                  │ workflow_dispatch
                    ┌─────────────▼───────────────┐
                    │ L1 会话面 SessionVault       │
                    │  refresh_token ──HTTP──▶ access_token
                    │  redirect/exchange ──▶ cookie 播种
                    │  纯 HTTP 探活（0 浏览器）     │
                    └─────────────┬───────────────┘
                                  │ 已登录 context
                    ┌─────────────▼───────────────┐
                    │ L2 领取面 ClaimFSM           │
                    │  真值快照 → 只领缺失 → checkpoint
                    │  帧守卫 · 单次提交 · 预算收尾  │
                    └─────────────┬───────────────┘
                                  │ 仅异常时
                    ┌─────────────▼───────────────┐
                    │ L3 验证码面 CaptchaPlane     │
                    │  hsw identity · 帧就绪        │
                    │  passive → 本地CV → 单模型 90s│
                    │  provider 健康门 · 到点即逃   │
                    └─────────────┬───────────────┘
                                  │ 三态结局
                    ┌─────────────▼───────────────┐
                    │ L4 证据与通知                │
                    │  scoreboard.json · 单图 · 日志│
                    │  claimed / already_owned /   │
                    │  needs_human(+预填链接)       │
                    └──────────────────────────────┘
```

**每周时序（目标）**

| 时间 | 动作 | 预期耗时 | 预期出题 |
| --- | --- | --- | --- |
| 周四 23:20（+外部心跳触发） | 会话面重建 → 领取 → 验证 → 通知 | ≤4 min | 0 |
| 周五 05:20（仅当周四非 claimed） | 补跑一次 | ≤4 min | 0–1 |
| 每天 6h 心跳 | 纯 HTTP 会话探活；异常才告警 | <2 s | 0 |
| 任一时刻 >8 天无 claimed | deadman 告警（含最近失败证据链接） | — | — |

**目标指标（可验收）**

| 指标 | 现状 | v3 目标 |
| --- | --- | --- |
| 连续 4 周真实领取成功 | 0/4（观测窗口内无一次真实领取） | **4/4** |
| 登录验证码出现率 | 每周 4 次冷启动，10/15 轮失败 | **≤1 次/月** |
| 单轮耗时（失败） | 12–35 min | **≤5 min** |
| `claimed_ok` 的可信度 | 3/3 空转 | 每条都有订单/权益证据 |
| 静默停摆 | 8 天无人知 | 8 小时内告警 |
| 货币成本 | 0 | **0** |

---

## 5. 落地计划

### P0（本周，1–2 小时，零设计风险）

| 任务 | 文件 | 验收 | 回滚 |
| --- | --- | --- | --- |
| P0-1 提交并推送工作区修复 | `.github/workflows/epic-gamer.yml`、`app/settings.py`、`app/services/run_outcome.py`、`app/deploy.py`、`app/services/browser_context.py`、`app/extensions/llm_adapter.py`、`docs/maintenance-log.md` | 远端出现 `epic-session-ok-`、`AUTH_MAX_ATTEMPTS: '3'`；一次 run 里有 `stage-events` 日志 | `git revert` |
| P0-2 手动 dispatch 复跑 | — | 日志出现 `Stage event \| stage=auth`；失败耗时 ≤7 min | — |
| P0-3 合上游 `7c3bfb5` + `70354be` | `app/extensions/hcaptcha_adapter.py`、`playwright_runtime.py`、`playwright_frame_guard.cjs`、`browser_context.py`、`epic_games_service.py` | 新 run `ns_error` 计数 0；`Cannot find a valid challenge frame` 0 | 保留 cherry-pick 前的 tag |
| P0-4 deadman 最小版 | 新增 `.github/workflows/heartbeat.yml` + `scripts/scoreboard.py` | 手工停跑 8 天能在通知里看到告警（用 `HEARTBEAT_FORCE_ALERT=1` 演练） | 删文件 |

### P1（两周内，核心改造）

| 任务 | 关键改动 | 验收 |
| --- | --- | --- |
| P1-1 验证层换 API | `_load_order_history_payload` → `page.request.get` + JSON 头；新增 `_check_entitlements` | 每轮 `order history` 命中不再 `JSONDecodeError`；`claimed` 有三态证据 |
| P1-2 三态结局 | `run_outcome.py` 增加 `claimed[]/already_owned[]/needs_human`；workflow summary 分支 | `In Library` 只产生 `already_owned` |
| P1-3 会话面 D1 | 新增 `app/services/session_vault.py`（token 轮换 + cookie 播种 + 探活）；`deploy.py` 里 auth 前置 | 冷启动（清空 profile）也能 0 验证码进 store |
| P1-4 provider 健康门 + 90s 决断 | `llm_adapter.py`、`epic_authorization_service.py` | 网关故障时 3 分钟内 `needs_human`，不再 8×155s |
| P1-5 预算收尾 | `deploy.py` 全局 deadline；`partial` 结局 | 无 60 分钟 cancelled |

### P2（观察期，1 个月）

- 家用 runner 执行面（`repository_dispatch` + 自托管 runner），对比 Actions 的出题率与成功率；
- 离线评测集扩容：现有 3 条合成样本（`challenge-set/`，`scores.json`）→ 用失败 artifact 里的真实题目构建 ≥30 条回放集，用于"改解题器前后"的 A/B，不再靠线上烧运行验证；
- 上游同步例行化（每两周 `git fetch ronchy` + 差异评审）。

---

## 6. 关键实验（决定路线的 3 个，每个 ≤1 小时）

> 这三个实验决定 D1 能不能成立。**在写任何生产代码之前先跑完**，失败就走 D1' 退路。

**E1：refresh_token 能不能换 access_token**

```bash
# 一次人工：device auth（launcher 公共 client；产出 device_code / user_code）
curl -s -u "<LAUNCHER_CLIENT_ID>:<LAUNCHER_CLIENT_SECRET>" \
  -d "grant_type=device_auth&device_id=<UUID>" \
  https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token
# 浏览器打开 verification_uri 输入 user_code → 拿到 refresh_token

# 之后每次：轮换（注意保存新的 refresh_token）
curl -s -u "<LAUNCHER_CLIENT_ID>:<LAUNCHER_CLIENT_SECRET>" \
  -d "grant_type=refresh_token&refresh_token=<RT>&token_type=eg1" \
  https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token
```

判定：返回 `access_token` 且 `expires_in≈7200` → 成立。

**E2：access_token 能不能换 web 登录 cookie**

```bash
curl -s -H "Authorization: bearer <ACCESS_TOKEN>" \
  "https://www.epicgames.com/id/api/redirect?clientId=<WEB_CLIENT_ID>&responseType=code"
# → {"authorizationCode":"..."}
curl -si "https://www.epicgames.com/id/api/exchange?code=<CODE>"   # 看 Set-Cookie
curl -s -b "<COOKIE_JAR>" -H "Accept: application/json" \
  "https://www.epicgames.com/account/v2/payment/ajaxGetOrderHistory"   # 期望 200 + orders[]
```

判定：`ajaxGetOrderHistory` 返回 JSON 且 `orders` 非空 → 成立（顺带验证了 D2 的验证端点）。

**E3：这套 cookie 能不能直接驱动 Playwright 上下文**

```python
# 伪代码：context.add_cookies([...从 E2 的 Set-Cookie 转换...]) → goto store
# 判定：页面右上角直接是账号头像，没有登录按钮，且 /account/v2/payment/ajaxGetOrderHistory 200
```

**如果 E2 失败**（Epic 对 web 交换加了指纹/设备校验），D1 降级为 **D1'**：

- 密码登录仍然保留，但把它**从"每周例行"改成"每月一次预授权"**：登录成功后立刻 dump cookie，存 secret；之后每次运行只做 cookie 注入 + keepalive 探活；
- keepalive 从"每周二一次"改成"每天一次纯 HTTP 探活 + 失效才登录"，把登录窗口压到最小；
- 同时把 D5 的家用 runner 提前到 P1（住宅 IP 的出题率显著更低）。

---

## 7. 明确不做

| 不做 | 理由 |
| --- | --- |
| 付费打码（2captcha/CapMonster） | 违背零预算；且 Talon 对第三方 token 支持存疑（v2 §3.3） |
| 住宅代理采购 | 按流量计费 > 收益；有家用 runner 这条路 |
| 纯 API 领取（不经浏览器） | `quickPurchase` 返回 `CHECKOUT`，`confirm-order` 需要 Talon 签发的 `captchaToken` + `xal` 指纹，Python 无法复刻（v2 §1.5，仍然成立） |
| 多账号批量刷 | 24h 领取限制 + 风控分叠加，收益线性而风险指数 |
| 把 `In Library` 当成功 | 这就是当前"假成功"的根因 |
| 继续给"更强的模型"加预算 | 失败主因是"出题"和"进不去"，不是"解题率不够"（v2 §2 A2，仍然成立） |

---

## 8. 与旧文档的关系

| 旧结论（v2 / refactor-plan-2026-10） | 本文处置 |
| --- | --- |
| §1.4 "会话有效则链路是稳的" | **保留并升级**：OK 日志证明 0 验证码；但"会话有效"要由 D1 主动制造，而不是靠缓存碰运气 |
| §1.5 "API 硬解不可行 / 机房 IP 是出题主因" | **保留**（D8 不做，D5 家用 runner 作为对策） |
| §4.1 题型路由（坐标走 CV、离散问模型） | **保留**，但优先级下降：出题率现在比解题率重要 |
| §4.2 周预算（登录 3 次/轮、验证码 ≤2 次/周） | **收紧**：登录 ≤1 次/月，验证码 ≤1 次/月 |
| §5 目标架构（Entry→AUTH→DISCOVER→CLAIM→VERIFY→NOTIFY） | **升级为四层 v3**（L0 调度守卫 / L1 会话 / L2 领取 / L3 验证码 / L4 证据通知），VERIFY 从"顺带"变成"第一类公民" |
| §5.2 SessionVault（profile + token 双通道） | **落地为 D1**：token 通道从"可选"变成"主通道" |

---

## 9. 附录：证据索引

```text
# 运行记录（全量 138 次，含结论与耗时）
python scripts/gh_runs_stats.py          # 走代理 127.0.0.1:10808
# 失败日志分类（15 份失败 + 3 份成功逐条统计）
python scripts/analyze_runlogs.py        # runlogs/*.log
# 上游差异
cd repo && git fetch ronchy && git diff --stat origin/master...ronchy/master
# 本地未提交修复
cd repo && git diff --stat
```

- 失败日志原文：`runlogs/36565377104.log` … `runlogs/38168649816.log`
- 空转成功日志：`runlogs/ok-37057555847.log`、`runlogs/ok-37145277172.log`、`runlogs/ok-37225282197.log`
- 上游仓库对比：`git remote add ronchy https://github.com/Ronchy2000/epic-freebies-helper.git`
- 关键代码位置：`app/services/epic_games_service.py`（`_load_order_history_payload` L257、`_should_ignore_task` L325、`collect_weekly_games` L2171）、`app/services/epic_authorization_service.py`（`_await_login_outcome` L395、`invoke` L843）、`.github/workflows/epic-gamer.yml`（L42–50 缓存、L149–152 重试预算）

---

## 10. 待决策项（需要你拍板）

1. **D1 是否先做实验 E1/E2**（需要你用真实账号跑一次 device auth，产出一个 refresh_token 存进 secret）——这是唯一需要你动手的部分。
2. **执行面**：只留在 GitHub Actions（简单、但机房 IP 出题率高），还是 P1 就上家用 runner（更好、但要求机器常开）。
3. **推送通道**：WXPush（微信，合上游）还是 Telegram（现有）作为主告警通道。
4. **是否接受"already_owned 也算 success"**：我建议算，但要在 summary 里显式区分，避免指标再次虚高。
