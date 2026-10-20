# Epic 周免助手重构计划 v2（零预算 / 免费模型版）

> 状态：草案 / 决策文档 **（v2 重写，取代 v1）**
> 仓库：`qq4756283/epic-freebies-helper`（fork 自 `Ronchy2000/epic-freebies-helper`，上游 `QIN2DIM/epic-awesome-gamer`）
> 约束：**总投入 0 元**。不购买打码服务、不购买住宅代理、不购买模型额度。
> 证据基线：GitHub Actions 运行记录（138 次）+ 仓库源码 + 2026-10 联网核实的免费模型现状

---

## 0. 相对 v1 的变更

v1 把"外部打码服务""住宅代理""付费云手机"写进了建议路径。**本版全部删除**，理由很简单：这个项目的收益是每周 1–2 款免费游戏，"为它付费"在经济学上不成立；一旦引入付费依赖，项目就从"自动领免费游戏"变成"为免费游戏付费"，失去存在意义。

因此 v2 的三条硬约束：

| 约束 | 含义 |
| --- | --- |
| **零货币成本** | 只使用免费额度、免费软件、已有硬件 |
| **零新增运维** | 不引入需要长期付费或长期看护的组件 |
| **失败必须便宜** | 一次失败的成本 = 一次 20 分钟 run + 一点风控分，不能是"再买一次额度" |

**免费不等于无限**。免费模型有速率限制、免费 runner 有时间限制、免费的代价是"不稳定"。所以 v2 的核心不是"堆更多模型"，而是**把模型调用当成稀缺预算来花**。

---

## 1. 证据基线（延续 v1，已核验）

### 1.1 运行数据

仓库最近 100 次 Actions 运行（总计 138 次）：

| 工作流 | success | failure | cancelled |
| --- | --- | --- | --- |
| Epic Awesome Gamer | 27 | **42** | 6 |
| Keepalive | 8 | 1 | — |
| Tests | 7 | 4 | — |

**领取成功率 27/75 ≈ 36%**；2026-10-08 → 10-11 连续 4 次失败。

### 1.2 失败根因（15/15 一致）

所有已归因失败都是 `outcome=auth_failed_captcha`，发生在**登录阶段**，`discover/claim/checkout` 从未执行。四类具体错误：

```
1) hsw script 注入失败: NS_ERROR_INVALID_CONTENT_ENCODING
2) Login captcha solve attempt failed | RetryError(... HTTPStatusError)   ← 免费/自建网关抖动
3) Login captcha solve attempt failed | AttributeError('NoneType' ... 'locator')
4) Timed out waiting for Epic login outcome
→ Epic Games authentication failed after 8 attempts
```

### 1.3 雪崩机制（最关键）

```yaml
- uses: actions/cache/save@v4
  if: always()        # 失败也保存
```

失败 run 把"未登录 profile"存成新缓存 → 下次 restore 到空档 → 重新登录 → 再出验证码 → 再失败。日志实证：成功 run 恢复的是上次成功的缓存（`epic-session-36977197596`），失败 run 恢复的是上次失败的缓存（`epic-session-38079483724`）。

### 1.4 成功 run 的特征

`Authentication completed` 后直接进入领取，**全程 0 次真实验证码**，结局 `outcome=claimed_ok`。→ 只要会话有效，链路是稳的。

### 1.5 外部结论（2026 联网核实，与预算无关但决定路线）

- **周免 BASE_GAME 纯 API 领取不可行**：`quickPurchase` 返回 `CHECKOUT`，`confirm-order` 需 Talon 签发的 `captchaToken`，`xal` 指纹无法纯 Python 复刻。
- **机房 IP 是出题主因**：住宅 IP + 真实桌面浏览器多为被动通过；Actions/Oracle/Azure 等机房 IP + Xvfb 几乎每次出图题。
- **主流项目已经放弃"硬解"**：`vogler/free-games-claimer`（4259★）靠 Firefox + 指纹注入 + 非 headless **不出题**；`claabs`、`onyxhq` 直接退化为"发预填链接"。

> 这三点在零预算下更成立：既然买不到"更可信的 IP"，就必须把"减少登录次数"和"减少出题率"做到极致。

---

## 2. 零预算下的设计公理

| 公理 | 推论 |
| --- | --- |
| **A1 免费模型是稀缺预算** | 每次调用都要记账；按题型分配最便宜的能力，禁止"先问大模型再说" |
| **A2 出题率 > 解题率** | 少出一次题，胜过把模型换强一倍（模型换强还要钱） |
| **A3 会话是最便宜的资产** | 一次有效会话 = 省掉一轮登录 = 省掉 N 次模型调用 = 省掉一次风控分 |
| **A4 失败要便宜** | 失败必须 5 分钟内结束并留下证据，不能耗满 20 分钟 + 8 次重试 |
| **A5 本地算力免费** | 已有 PC/NAS 的 CPU 推理（本地 CV、小 VLM）不花钱，应优先使用 |
| **A6 可降级 > 可成功** | "发个链接让人点一下"是合法终点，不是失败 |

---

## 3. 免费资源盘（2026-10 核实）

### 3.1 免费视觉模型

| 来源 | 模型 | 免费性质 | 视觉能力 | 适配 hCaptcha | 备注 |
| --- | --- | --- | --- | --- | --- |
| 智谱开放平台 | `glm-4.6v-flash` | **免费**，视觉推理 | 128K / 32K 输出 | 强，但高峰 429（`code=1305`） | **时段性可用**，仅在未限流时处理复杂语义题 |
| 智谱开放平台 | `glm-4.1v-thinking-flash` | **免费**，思考模式视觉推理 | 64K / 16K 输出 | **实测最稳最准（选格 2/2）** | **默认主力**（4.6v 高峰不可用时的替代） |
| 智谱开放平台 | `glm-4v-flash` | **免费**，图像理解 | 16K / 1K 输出 | 简单二分类、图像描述 | 上下文最小，仅单图短问 |
| 智谱开放平台 | `glm-5.3-flash` / `flashx` | **不是免费**，但属极低成本档（原生多模态，1M 上下文） | 强 | 高 | 仅作为"免费额度耗尽后的最后选择"，默认关闭 |
| OpenRouter | `google/gemma-4-31b-it:free`、`gemma-4-26b-a4b-it:free` | 免费档（有速率限制） | 图像输入 | 中 | 直接可用，无需信用卡 |
| OpenRouter | `nvidia/nemotron-3.5-super-vl:free` | 免费档 | 视觉语言 | 中高 | 大上下文，响应偏慢 |
| OpenRouter | `minimax/minimax-m3.1-flash:free` | 免费档 | 多模态 | 中 | 速度快 |
| OpenRouter | `dots-studio/dots-3-note-preview:free`、`thinkingmachines/inkling[-small]:free` | 免费档 | 部分含视觉 | 待评分 | 新模型，稳定性未知 |
| 本地 Ollama | `qwen3-vl:2b` / `4b` / `8b` | **完全免费、无限次**（吃本机 CPU/GPU） | 原生视觉 + 2D grounding（相对坐标）+ GUI 元素识别 | **高**（专门优化过 GUI/空间定位） | 2B 仅 1.9GB，8B 需 6.2GB 显存/内存 |
| 已有自建中转 | `workbuddy/global/gpt-6-astra`（当前在用） | 现状 | 能用 | 中 | 明文 HTTP、无 SLA，**降级为可选**，不再作为唯一 provider |

> 说明：OpenRouter 免费档的具体每分钟/每天请求数以其官方 limits 页为准，且会变化；设计上按**保守假设 50 次/天**做预算，不依赖"能无限刷"。

#### 3.1.1 接入实测：2026-10-20（Key 已生效，含量化结论）

第一把 Key（32 位 hex、无 `.` 分隔）在四个端点全部 401，判定为套餐 Key 而非标准 Key；换成标准格式（`{id}.{secret}`）后立即生效。以下数据全部由 `scripts/probe_llm_pool.py` 实测，每模型 2 次。

**任务 A：3×3 网格多选**（对应 `image_label_multi_select`，答案空间离散）

```
provider   model                            status       ms  tokens  result
zhipu      glm-4.6v-flash                      429      323       -  {"code":"1305","message":"该模型当前访问量过大"}
zhipu      glm-4.6v-flash                      429      217       -  同上
zhipu      glm-4.1v-thinking-flash             200     2020     420  cells=[2, 6] correct=True
zhipu      glm-4.1v-thinking-flash             200     2249     458  cells=[2, 6] correct=True
zhipu      glm-4v-flash                        200     5174     321  cells=[2, 6] correct=True
zhipu      glm-4v-flash                        200      728     321  cells=[2, 6] correct=True
```

**任务 B：自由坐标点选**（对应 `image_drag_*` / `image_label_area_select`，答案空间连续）

```
zhipu      glm-4.6v-flash                      429      327       -  {"code":"1305"}
zhipu      glm-4.6v-flash                      429      226       -  {"code":"1305"}
zhipu      glm-4.1v-thinking-flash             200     3595    1577  count=2 err_px=[66.9, 274.1]
zhipu      glm-4.1v-thinking-flash             200     4535    1695  count=2 err_px=[67.7, 274.8]
zhipu      glm-4v-flash                        200     1684    1342  count=2 err_px=[442.8, 161.6]
zhipu      glm-4v-flash                        200     4142    1342  count=2 err_px=[442.8, 161.6]
```

汇总：

| 模型 | 可用性 | 网格多选 | 坐标点选（1280×720） | 延迟 p50 | 单次 token |
| --- | --- | --- | --- | --- | --- |
| `glm-4.1v-thinking-flash` | **2/2 稳定** | **2/2 正确** | 66.9 / 274.1 px（一个准、一个偏） | 2.2–4.5 s | 420–1695 |
| `glm-4v-flash` | 2/2 稳定 | **2/2 正确** | 442.8 / 161.6 px（**两次结果完全相同 → 在猜**） | 0.7–5.2 s | 321–1342 |
| `glm-4.6v-flash` | **0/4，持续 429 `code=1305`** | 无样本 | 无样本 | — | — |

三条结论（直接改写 §4.1）：

1. **免费模型的"数数 / 选格"可靠，"定位坐标"不可靠。** 两个模型都能准确指出"哪两格有红点"，但自由坐标误差 67–443 px。→ **离散答案交给模型，连续坐标交给本地 CV。**
2. **`glm-4v-flash` 的坐标是编的**：两次请求返回完全相同的 `[[640,100],[960,650]]`，是固定网格猜测，不是观察。→ 绝不能用于拖拽 / 框选。
3. **`glm-4.6v-flash` 在 18:50–18:55 连续 4 次 429（`code=1305`「该模型当前访问量过大」）**：规格最强（128K），但免费高峰不可用。→ 只能写成"时段性可用"，不能当主力。

局限：本次用合成图，真实 hCaptcha 是照片级语义图，难度更高。真实评测集应来自 artifact 里的历史 challenge 截图（§7 想法 #12）。本节结论只用于**确定路由优先级**，不代表真实通过率。

### 3.2 免费算力与基础设施

| 资源 | 免费额度 | 用途 |
| --- | --- | --- |
| GitHub Actions（public 仓库） | 免费、无分钟上限 | 发现、调度、汇总、artifact |
| GitHub Actions（private 仓库） | Free 计划约 2000 分钟/月 | 同上（注意 20 分钟/次的成本） |
| self-hosted runner | **完全免费**（自家机器） | 登录 + 领取（住宅 IP + 持久 profile） |
| Actions cache | 公开仓库 10GB 级别 | 会话 profile / token / 评测集 |
| 仓库内置本地 CV | 0 元 | 拖拽、连线、数字题（已有 1143 行实现） |
| 手机 / 平板 | 0 元 | 人机协作兜底（Telegram 点一下） |

### 3.3 明确不采用的付费项

| 项 | 原因 |
| --- | --- |
| 2captcha / capsolver / CapMonster | 按次计费；且 Talon 可能不认第三方 token（外部实测 CapMonster 直接不支持 hCaptcha） |
| 住宅代理（BrightData 等） | 按流量计费，登录流程 5–20MB/次，长期成本 > 收益 |
| 云手机 / 云桌面 | 月租；本质与 self-hosted runner 重复 |
| 更强模型（付费档） | 不解决"出题率"，只提高"解题率"，投入产出比最低 |

---

## 4. 免费额度下的模型路由（本版核心）

### 4.1 按题型分配（不按"模型强弱"分配）

| hCaptcha 题型 | 首选 | 次选 | 为什么（含实测依据） |
| --- | --- | --- | --- |
| `image_drag_single` / `image_drag_multi` | **本地 CV**（轮廓/线段/数字模板匹配，已有） | `glm-4.6v-flash` 仅做"是否已对齐"的语义判断 | 实测两个模型坐标误差 67–443px，**不能用于拖拽** |
| numbered line / 连线题 | **`numbered_line_solver.py`**（已有模板匹配） | 本地 VLM | 纯几何题，模型反而容易错 |
| `image_label_binary` | `glm-4v-flash`（16K，最省） | `glm-4.1v-thinking-flash` | 二分类只需"看一眼"，321 token 即可 |
| `image_label_multi_select` | **`glm-4.1v-thinking-flash`**（实测 2/2 正确） | `glm-4v-flash`（实测 2/2 正确） | 离散答案空间，免费模型够用 |
| `image_label_area_select` | 本地 CV 定框 + 模型语义校验 | `glm-4.6v-flash`（未限流时） | 连续坐标不可信，只能靠 CV |
| 需要多轮推理的难题 | `glm-4.6v-flash`（128K，**仅当未 429**） | `skip` | 规格最强但高峰不可用，不能当主力 |
| 未知/新题型 | 先 `passive` 等待，再 `skip` | — | 不为未知题型烧额度 |

智谱三个免费模型的实测定位（2026-10-20，非推测）：

| 模型 | 上下文 | 实测可用性 | 实测能力 | 定位 |
| --- | --- | --- | --- | --- |
| `glm-4.1v-thinking-flash` | 64K / 16K 输出 | **稳定**（2/2，无 429） | 选格 2/2；坐标 67px 级 | **默认主力**（因 4.6v 限流） |
| `glm-4v-flash` | 16K / 1K 输出 | 稳定 | 选格 2/2；坐标**乱猜**（两次相同） | 简单题 + 快速兜底 |
| `glm-4.6v-flash` | 128K / 32K 输出 | **时段性**（本次 4 次全 429 `code=1305`） | 无样本 | 只在未限流时处理复杂语义题 |

原则：**能用代码算的，绝不问模型；能用离散答案解决的，绝不要求坐标。**

### 4.2 预算表（每周）

按"每周四主跑 + 周五/周六补跑"三次运行设计：

| 预算项 | 每周上限 | 说明 |
| --- | --- | --- |
| 登录尝试 | 3 次/轮，最多 2 轮 | 超过即判定"IP/身份问题"，停止 |
| 验证码出现次数 | 目标 ≤ 2 次/周 | 靠会话复用 + 被动通过 |
| 免费云模型调用 | 40 次/周 | 约 13 次/轮，够 2–3 道题的多轮尝试 |
| 本地模型调用 | 不限 | 只受单次耗时约束（2B 约 3–8s/题） |
| 单题尝试上限 | 3 次（跨 provider 累计） | 超过即 `skip`，记录证据 |
| 整轮墙钟 | 25 分钟 | Actions 免费额度不是无限的（private 更要注意） |

### 4.3 额度耗尽 / 限流降级链

```
passive（等 5–10s，看是否自动过）
  ↓ 未过
local_cv（0 成本，唯一可信的坐标来源）
  ↓ 未过
本地 VLM qwen3-vl:2b（0 成本，慢）
  ↓ 未过
glm-4v-flash（16K，最快，实测稳定）
  ↓ 未过
glm-4.1v-thinking-flash（64K，实测最准，默认主力）
  ↓ 未过
glm-4.6v-flash（128K，仅当未返回 1305）
  ↓ 未过
OpenRouter 免费档轮转（gemma-4:free / nemotron:free）
  ↓ 429（含智谱 code=1305）/ 额度耗尽 / 连续 2 次超时
skip → 记录证据 → 继续其他 offer → 通知（附截图）
```

排序依据（实测）：**先快后准，先离散后连续**。`glm-4v-flash` 最快最省（321 token），`glm-4.1v-thinking-flash` 最准但慢一倍，`glm-4.6v-flash` 规格最高但高峰期基本拿不到。

关键：**429 不是错误，是信号**。智谱的限流不是标准 429 语义，而是 HTTP 429 + `code=1305`「该模型当前访问量过大」，必须按 body 判断，不能只看状态码；收到即冷却该 provider 并切下一个。

### 4.4 免费 provider 的健康管理

```python
@dataclass
class ProviderState:
    name: str
    cooldown_until: float = 0.0     # 429 后冷却
    consecutive_errors: int = 0     # 连续失败计数
    calls_today: int = 0
    daily_cap: int = 50             # 保守值

    def available(self, now: float) -> bool:
        return now >= self.cooldown_until and self.calls_today < self.daily_cap
```

规则：

1. 连续 2 次失败 → 冷却 30 分钟；
2. 连续 3 次失败 → 本轮禁用；
3. 全部 provider 不可用 → 直接 `skip`，不消耗登录重试预算；
4. 每次调用记录 `(provider, model, 题型, 耗时, tokens, 结果)`，用于每周选型复盘。

---

## 5. 目标架构（零成本版）

```
Entry (deploy.py)
  └─ Orchestrator: RunState + Stage + Budget + Checkpoint
       ├─ AUTH     SessionVault（profile + token 双通道）→ 多数轮次跳过登录
       ├─ DISCOVER 纯 HTTP（0 成本、不需要登录、可离线）
       ├─ CLAIM    浏览器状态机（只在必要时启动）
       │            └─ CaptchaRouter: passive → local_cv → local_vlm → free_cloud → skip
       ├─ VERIFY   entitlement API 真值核对
       └─ NOTIFY   Telegram / Actions summary / 预填链接（兜底）
```

### 5.1 与 v1 的差异

| 项 | v1 | v2 |
| --- | --- | --- |
| Captcha 链 | passive → local_cv → llm → **external（付费）** → human | passive → local_cv → local_vlm → **免费云** → skip（**无付费项**） |
| 模型层 | provider 抽象（含付费 provider） | **免费 provider 池 + 配额与冷却** |
| 拓扑推荐 | 混合（可含住宅代理） | **self-hosted runner（0 元）** 或通知型 |
| 预算模型 | 时间/调用/崩溃 | 增加**免费额度账本**（按天/周/题型） |
| 验收指标 | 成功率 | 成功率 + **单周货币成本 = 0** |

### 5.2 SessionVault（仍是最高 ROI）

```
session/
├── profiles/<account>.playwright/   # 持久 profile（.profile-ok 只在 VERIFY 成功后写）
├── tokens/<account>.json            # access/refresh/expires_at
└── meta.json                        # 上次成功时间、健康位、版本
```

三条规则（与 v1 相同，因为这一步不花钱且收益最大）：

1. 成功才写 ok 标记（当前是登录成功即写，导致"登录成功、领取失败"的 profile 被信任）；
2. **失败 profile 不覆盖成功 profile**（缓存双 key：`epic-session-ok` / `epic-session-last`）；
3. token 与 profile 双通道，任一可用即跳过登录页。

### 5.3 免费模型接入矩阵（填 key 即用）

```ini
# 免费池：按顺序尝试，前一个 429/超时才用下一个
# 智谱三个免费视觉模型（同一把标准 API Key，靠模型名区分上下文与能力）
GLM_API_KEY=<标准 API Key，格式 {id}.{secret}；不是 Coding Plan 套餐 Key>
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLM_MODEL=glm-4.1v-thinking-flash     # 默认主力（实测唯一稳定且准确）
FREE_VLM_1=glm-4v-flash              # 16K  / 1K 输出，最快最省，稳定
FREE_VLM_2=glm-4.1v-thinking-flash   # 64K  / 16K 输出，最准，默认
FREE_VLM_3=glm-4.6v-flash            # 128K / 32K 输出，时段性可用（高峰 1305）

FREE_VLM_4=google/gemma-4-31b-it:free  # OpenRouter 免费档（可选）
FREE_VLM_4_BASE=https://openrouter.ai/api/v1
FREE_VLM_4_KEY=<你的 OpenRouter key>

FREE_VLM_3=nvidia/nemotron-3.5-super-vl:free

# 本地：不限次，只在 self-hosted / 本机启用
LOCAL_VLM=qwen3-vl:2b
LOCAL_VLM_BASE=http://127.0.0.1:11434

# 每日配额（保守值，按官方 limits 页调整）
FREE_VLM_DAILY_CAP=50
```

> 你手上有什么免费模型，就填到 `FREE_VLM_*` 槽位；路由表只认"槽位顺序"，不认具体厂商。新增一个 provider 不需要改业务流程。

---

## 6. 运行拓扑（全部 0 元）

| | **T-A self-hosted runner（推荐）** | T-B Actions-only（止血） | T-C 通知型（下限） |
| --- | --- | --- | --- |
| 发现 | Actions（免费） | Actions | Actions |
| 登录/领取 | 自家机器（住宅 IP + 持久 profile） | 机房 IP | 不做 |
| 验证码 | 多为被动通过 | 频繁出题 | 无 |
| 模型 | 本地 qwen3-vl（0 元）+ 免费云兜底 | 免费云（额度紧张） | 无 |
| 货币成本 | 0（电费忽略） | 0 | 0 |
| 预期成功率 | **80–95%** | 35–50% | N/A（点一下） |
| 前置条件 | 一台常开的 PC/NAS/旧笔记本 | 无 | 无 |
| 维护 | 中低 | 低 | 极低 |

**T-A 的落地方式（改动最小）**：把 workflow 的 `runs-on: ubuntu-latest` 改成 `runs-on: [self-hosted, epic]`，在家用机器装 runner，其余不变。这是"零预算下提升成功率"唯一有效的一步——因为它同时解决 IP、会话、模型三件事。

如果不方便常开机器 → 直接走 T-C：Actions 每天发现新周免，发一条带**预填链接**的通知，人点一下。零风险、零维护、成功率 100%（人操作）。

---

## 7. 想法清单（全部零成本，按 ROI）

| # | 想法 | 解决 | 工时 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | 缓存双 key + 成功才写 ok | 雪崩 | 1 天 | 已确认是 4 连败主因 |
| 2 | 登录失败降级通知（不 raise） | 整轮不可用 | 0.5 天 | 直接把 20 分钟 → 5 分钟 |
| 3 | stage 级 outcome + 验证码计数 | 无法定位 | 1 天 | 后续所有优化的前提 |
| 4 | self-hosted runner | IP/会话/模型 | 0.5 天 | 免费，收益最大 |
| 5 | token 双通道 + refresh | 登录频率 | 3 天 | 目标：登录 ≤ 2 次/月 |
| 6 | CaptchaRouter + skip | 单点失败 | 2 天 | skip 是合法终点 |
| 7 | 本地 qwen3-vl:2b 接入 | 模型依赖 | 2 天 | 0 元、无限次、GUI 定位强 |
| 8 | 本地 CV 扩展（点选/计数） | 云模型额度 | 3 天 | 已有 1143 行可复用 |
| 9 | 免费 provider 配额账本 | 429 雪崩 | 1 天 | 按天/题型记账 |
| 10 | 免责/降级：预填链接通知 | 最后一公里 | 1 天 | 用户 10 秒点完 |
| 11 | entitlement API 复核 | 误报成功 | 1 天 | 真值，不靠页面文案 |
| 12 | 离线 challenge 回放台 | 模型选型靠猜 | 2 天 | 用历史 artifact 截图做评测集 |
| 13 | 人机协作（Telegram 点选） | 难题兜底 | 2 天 | 手机点一下，0 元 |
| 14 | 每周复盘报告 | 长期可维护 | 1 天 | 成功率/额度/题型分布 |

**特别推荐 #12（离线回放台）**：仓库 artifact 里已经有历史 challenge 截图（`app/volumes/hcaptcha/.challenge/`）。把这些截图 + 正确答案整理成一个小评测集，就能在**不烧线上额度**的前提下给 `glm-4v-flash`、`gemma-4:free`、`qwen3-vl:2b` 打分，直接决定 §4.1 的路由表。零成本，且让"选模型"从玄学变成数据。

---

## 8. 分阶段计划（每一阶段都是 0 元）

### P0（已完成，2026-10-20）：止血 + 立数据

| 任务 | 实现位置 | 状态 |
| --- | --- | --- |
| 缓存双 key（ok / last），失败不覆盖成功 | `.github/workflows/epic-gamer.yml`：restore 顺序 `epic-session-ok-` → `epic-session-last-` → `epic-session-`；`success()` 存 ok，`failure()` 存 last，`cancelled` 不存 | ✅ |
| profile 健康标记语义校正 | `browser_context.py`：新增 `PROFILE_VERIFIED_MARKER` + `mark_active_profile_verified()`，由 deploy 在 claim 结束后写 | ✅（方案已修正，见下） |
| 登录失败 → 明确 outcome + 快速失败 | `deploy.py`：`write_run_outcome(..., stage="auth")` + `write_stage_event`，异常消息带上实际尝试次数与原因 | ✅ |
| `AUTH_MAX_ATTEMPTS` 8 → 3 | `settings.py` 默认 3（`ge=3, le=5`）+ workflow env 默认 `'3'` | ✅ |
| 智谱限流冷却（含 `code=1305`） | `llm_adapter.py`：`GLM_RATE_LIMIT_CODES` 加入 `1305`，`start_model_cooldown()` 按 `模型|端点` 冷却 90s（`GLM_RATE_LIMIT_COOLDOWN_SECONDS` 可调），冷却期内抛 `GlmRateLimitedError` 不发请求 | ✅ |
| stage 级 outcome 埋点 | `run_outcome.py`：`write_run_outcome(..., stage=)` + `write_stage_event()` 追加 `stage-events.jsonl` + `read_stage_events()`；`deploy.py` 在 auth / claim / verify 打点 | ✅ |

**实施中发现并修正的一处原方案（重要）**

原计划写的是"`.profile-ok` 只在 VERIFY 成功时写"，读代码后发现这是**反向优化**：

`ensure_healthy_profile()` 的判定是"**没有 marker 就清空 profile**"。如果把 marker 改成只在领取成功后才写，那么"登录成功、但这次没领到"的 profile 会被当作脏数据清空 —— 丢掉一个**有效会话**，下次必须重新登录，反而更容易撞上验证码。

真正的雪崩源是 workflow 的 `if: always()`（失败也保存），而不是 marker 语义。所以：

- `PROFILE_HEALTH_MARKER`（`.profile-ok`）保持原语义：**登录成功即有效**（会话就是资产）；
- 新增 `PROFILE_VERIFIED_MARKER`（`.profile-verified`）只做观测，用于事后区分"登录了但没领到"和"登录并领到了"；
- 隔离失败档的责任交给缓存双 key。

**P0 的目标不是提高成功率，而是让失败"便宜且可读"**：失败从 15–20 分钟压到 ≤ 6 分钟，且每条失败都能落到具体 stage。

### P1（2–3 周）：把"登录"变成低频事件

| 任务 | 验收 |
| --- | --- |
| `SessionVault`（profile + token） | 登录次数 ≤ 2 次/月 |
| `discovery` 与 `claim` 解耦 | 发现可在无凭据环境跑 |
| `CaptchaRouter` + `passive/skip` | 每次挑战有 `solved_by` 记录 |
| 本地 `qwen3-vl:2b` 接入 | 本地解题占比 ≥ 50% |
| 离线回放台 | 三个 provider 有对比分 |

### P2（按需）

- self-hosted runner 切换（若还没切）
- 本地 CV 覆盖点选题
- 人机协作通道
- 通知型兜底开关（`FALLBACK_NOTIFY_ONLY=true`）

### 回滚

所有新路径用 env 开关，默认保留旧行为：`SESSION_DUAL_KEY`、`CAPTCHA_CHAIN`、`LOCAL_VLM_ENABLED`、`FREE_VLM_DAILY_CAP`。出问题改 env，不 revert 代码。

---

## 9. 验收指标

| 指标 | 现状 | P0 | P1 后 |
| --- | --- | --- | --- |
| 周领取成功率 | 36% | 45% | ≥ 80%（T-A） |
| 登录次数/月 | ~12 | ~8 | ≤ 2 |
| 单轮耗时（失败） | 15–20 min | ≤ 6 min | ≤ 5 min |
| 免费云模型调用/周 | 不可测 | 可测 | ≤ 40 |
| **货币成本/周** | 0（但含付费建议） | **0** | **0** |
| 失败可归因率 | 低 | 100% | 100% |
| 人工介入 | 每周 | 每两周 | 每月 |

---

## 10. 风险

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 免费模型限流 | 429 频繁，尤其 OpenRouter 免费档 | 配额账本 + 冷却 + 本地 VLM 兜底 |
| 免费模型下线 | 免费策略随时变 | provider 槽位化，换一个只改 env |
| 本地模型算力 | 2B 精度有限，8B 需内存 | 按题型分配；CV 优先 |
| self-hosted 可用性 | 机器关机则无人领取 | Actions 侧发现 + 失败通知 + 预填链接 |
| 账号风控 | 高频登录提高风险分 | 会话复用、重试降到 3、固定出口 |
| 条款 | 自动化可能违反 ToS | 仅个人账号、低频、不转售；保留免责声明 |
| 隐私 | challenge 截图含账号信息 | artifact 保留 7 天；评测集脱敏 |

---

## 11. 附录

### 11.1 验证免费模型是否可用（0 成本）

仓库内已带探针脚本 `scripts/probe_llm_pool.py`：它生成一张已知坐标的合成图（两个红点 + 干扰元素），对免费池逐个 provider 发同一个视觉请求，报告**连通性、延迟、token 消耗、JSON 合规性、像素误差**。这就是 §4.1 路由表的数据来源。

```powershell
# 只测智谱三个免费模型（需要标准 API Key，格式 {id}.{secret}）
$env:GLM_KEY = "<标准 api key>"
uv run python scripts/probe_llm_pool.py

# 测"网格多选"题型（最贴近 image_label_multi_select），每模型跑 2 次看稳定性
uv run python scripts/probe_llm_pool.py --task grid --repeat 2

# 同时测 OpenRouter 免费档 / 本地 Ollama
$env:OR_KEY = "<openrouter key>"
$env:LOCAL_VLM_BASE = "http://127.0.0.1:11434"
uv run python scripts/probe_llm_pool.py --out probe.json

# 只测指定模型
uv run python scripts/probe_llm_pool.py --models glm-4.1v-thinking-flash
```

判读标准：

| 观察 | 含义 |
| --- | --- |
| `status=401` | Key 无效/类型不对（标准 API Key ≠ 套餐 Key），与模型名无关 |
| `status=200` 但 `no json object` | 模型"爱说话"，需要在 prompt 里强约束或改 `response_format` |
| `err_px` 中位数 > 80 | 坐标不可用于点选/框选，只能用于语义判断 |
| `latency_ms` > 20000 | 会吃掉整轮预算，应降级到只用简单题 |

### 11.2 复核历史失败的命令

```bash
gh api "repos/qq4756283/epic-freebies-helper/actions/runs?per_page=100" \
  --jq '.workflow_runs[] | "\(.name)\t\(.conclusion)\t\(.created_at)"'

gh run view <RUN_ID> --repo qq4756283/epic-freebies-helper --log-failed \
  | grep -E "outcome=|Authentication failed|Login captcha|Cache restored"
```

### 11.3 关键源码位置

| 位置 | 行数 | 说明 |
| --- | --- | --- |
| `app/services/epic_games_service.py` | 2227 | 待拆分（discovery / claim / verify） |
| `app/services/epic_authorization_service.py` | 888 | 登录与状态监听 |
| `app/extensions/llm_adapter.py` | 1406 | provider 适配（改造为免费池路由） |
| `app/extensions/hcaptcha_adapter.py` | 887 | 本地 CV 与补丁 |
| `app/extensions/numbered_line_solver.py` | 256 | 数字连线题本地求解 |
| `app/services/browser_context.py` | 325 | profile 健康、后端选择 |
| `app/services/run_outcome.py` | 61 | 待扩展为 stage 级 |

### 11.4 免费模型现状来源（2026-10）

- 智谱三个免费视觉模型（项目所有者提供，2026-10-20）：
  - `glm-4.6v-flash`：免费，视觉推理，128K 上下文 / 32K 输出
  - `glm-4.1v-thinking-flash`：免费，视觉推理（思考模式），64K / 16K
  - `glm-4v-flash`：免费，图像理解，16K / 1K（官方页标注"首个完全免费的图像理解模型"）
- 智谱 GLM-5.3-Flash/FlashX：原生多模态、极低成本（**非免费**，默认不启用）
- 智谱 Key 分类公告（2026-10-16 生效）：标准 API Key / 个人套餐 Key / 团队套餐 Key 分开管理，互不通用；调用端点决定历史兼容 Key 消耗哪类额度
- OpenRouter `/api/v1/models` 实测：470 个模型中 20 个 pricing=0，其中含图像输入的 12 个（`gemma-4-31b-it:free`、`nemotron-3.5-super-vl:free`、`minimax-m3.1-flash:free` 等）
- Ollama `qwen3-vl`：2b/4b/8b/30b/32b/235b，原生视觉 + 2D grounding + GUI 元素识别

> 免费额度与速率限制会变，接入前请以各平台控制台/limits 页为准；本文的 50 次/天是保守假设，不是官方承诺。

---

## 12. ADR 摘要（v2）

| 决策 | 结论 | 依据 |
| --- | --- | --- |
| 是否引入付费打码/代理/模型 | **不引入** | 收益（每周 1–2 款免费游戏）< 成本 |
| 主模型 | **`glm-4.1v-thinking-flash`（实测唯一稳定且准确）为主，`glm-4v-flash` 打简单题，`glm-4.6v-flash` 仅时段性使用；本地 `qwen3-vl` 补零成本算力** | 2026-10-20 实测：4.6v 连续 429，4.1v-thinking 2/2 正确 |
| 主拓扑 | **self-hosted runner（T-A）**，否则通知型（T-C） | 0 元且同时解决 IP/会话/模型 |
| 是否继续用当前自建中转 | 降级为可选 | 明文 HTTP、无 SLA，是失败放大器 |
| 是否纯 API 领取 | 否 | 周免 BASE_GAME 结构性拒绝 |
| 验证码策略 | passive → local_cv → local_vlm → 免费云（16K→128K→thinking）→ skip | 成本递增；skip 合法 |
| 是否提高重试次数 | 否，8 → 3 | 失败成本必须便宜 |
| 免费模型选型方法 | **先跑 `scripts/probe_llm_pool.py` 量测，再写路由表** | 避免"凭感觉换模型" |

---

_本版基于 2026-10-20 数据；免费模型的可用性按季度复核即可，业务逻辑不应绑定具体模型名。_
