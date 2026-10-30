# 外部 cron 触发 + deadman 心跳：15 分钟速配指南

> 目的：GitHub 的 `schedule` 会**静默丢触发**（官方文档原话："If the load is sufficiently high
> enough, some queued jobs may be dropped"；本仓库 2026-10-14 起连续 8 天 0 触发即为实例）。
> 本指南用两个免费服务把"定时"从 GitHub 手里拿回来，并在连续失败时主动告警。
>
> 成本：0 元。一次性投入：约 15 分钟。之后无需人工巡检。

---

## 1. 创建 PAT（GitHub Personal Access Token）

1. 打开 <https://github.com/settings/personal-access-tokens/new>（fine-grained token）。
2. **Repository access** → Only select repositories → 勾选 `qq4756283/epic-freebies-helper`。
3. **Permissions** → Repository permissions → **Actions: Read and write**（其余保持 No access）。
4. Expiration：建议 **90 天**（到期前 cron-job.org 的失败邮件会提醒你换）。
5. 生成后立刻复制 `github_pat_...`，只在这一步能看到。

> 最小权限原则：这个 token 只能触发 Actions，不能读写代码、不能碰 secrets。
> 万一泄露，攻击者最多能刷你的 Actions 分钟数（public 仓库免费），改不了代码。

---

## 2. cron-job.org：把触发权从 GitHub 拿回来

1. 注册 <https://cron-job.org>（免费，捐助运营，任务数不限，最快每分钟一次）。
2. 右上角 **Create cronjob**，按下面填**两个**任务（周四一个、周五一个）：

| 字段 | 值 |
| --- | --- |
| Title | `epic-freebies Thu` / `epic-freebies Fri` |
| URL | `https://api.github.com/repos/qq4756283/epic-freebies-helper/actions/workflows/epic-gamer.yml/dispatches` |
| Schedule | 自定义：**周四 17:00**、**周五 17:00**（时区选 **UTC**） |
| Request method | `POST` |
| Request body | `{"ref":"master"}` |
| Headers | `Authorization: Bearer github_pat_...`<br>`Content-Type: application/json`<br>`Accept: application/vnd.github+json` |

3. 保存后点 **TEST RUN**，应看到 **HTTP 204 No Content**（= 已入队）。同时 GitHub 的
   Actions 页面会立刻出现一条 `workflow_dispatch` 运行。
4. 打开该任务的 **Notifications**，勾选 **"Notify me when the job fails"**（邮件）。
   cron-job.org 连续失败 25 次会自动停用任务——这是 PAT 过期后的兜底信号。

> **为什么是 17:00 UTC 而不是 16:00**：Epic 周免在周四 16:00 UTC 切换，整点是 GitHub
> 队列高峰（官方文档点名 "the start of every hour"）。晚 1 小时触发既避开高峰，又保证
> 商品已切换完成。

---

## 3. 与现有机制如何协作（不会打架）

| 触发源 | 作用 | 备注 |
| --- | --- | --- |
| GitHub `schedule`（每天 4 槽位） | 第三重冗余 | 丢触发时靠第 2 节的两个外部任务补上 |
| cron-job.org（周四/周五） | 主触发 | 绕开 GitHub 调度队列 |
| 手动 `workflow_dispatch` | 紧急兜底 | `force=1` 可绕过闸门 |

**重复触发是无害的**：`scripts/gate.py` 是幂等闸门，它按 `docs/scoreboard.json` 判断——

- 本周已确认领取 → 直接跳过（10 秒结束，不启动浏览器）；
- 最近 `ATTEMPT_COOLDOWN_HOURS`（当前 **12 小时**）内已尝试过登录 → 跳过。

所以哪怕外部 cron 和 schedule 同一小时都触发，也只会真跑一次。

---

## 4. deadman 心跳：机器"死了"要在 24 小时内知道，而不是 8 天后

最致命的失败不是验证码，而是**静默死亡**：workflow 不再被触发、或每轮都失败但没人发现。
两种免费做法，任选其一：

**A. 用 cron-job.org 的失败通知（最简单）**
第 2 节的邮件通知已经覆盖"dispatch API 调不通"这一类。但它看不到"workflow 跑了却失败"。

**B. UptimeRobot 心跳（推荐，免费档够用）**

1. 注册 <https://uptimerobot.com>，选 **New Monitor → Heartbeat**。
2. 周期设为 **1 天**，宽限期 **2 天**（即"48 小时没收到 ping 就告警"）。
3. 复制它给的 ping URL（形如 `https://heartbeat.uptimerobot.com/xxxxx`）。
4. 在仓库里加一个"成功才 ping"的步骤（示例，放在 `epic-gamer.yml` 的成功分支）：

```yaml
      - name: Deadman heartbeat
        if: success()
        run: curl -fsS --max-time 10 "https://heartbeat.uptimerobot.com/xxxxx" || true
```

> 注意：UptimeRobot 免费档的**自定义 Header 与 HTTP method 是 Pro 功能**，所以它
> **只能做监控，不能做触发**——触发请用 cron-job.org。

**C. 零配置替代**：每周手动看一眼 <https://github.com/qq4756283/epic-freebies-helper/actions>
或 `docs/scoreboard.json` 最后一条记录的时间戳。低于"7 天"就说明调度或登录出了问题。

---

## 5. 常见坑

| 坑 | 说明 | 对策 |
| --- | --- | --- |
| 整点高峰 | 官方点名 "start of every hour" 是高峰 | 用 :17 这类非整点（本指南已避开） |
| 时区 | cron-job.org 默认按你账号时区解释时间 | 建任务时明确选 **UTC** |
| PAT 过期 | 90 天后 dispatch 全部 401 | 开失败邮件；25 连败自动停用是信号 |
| workflow 必须在默认分支 | dispatch API 只认默认分支上的 workflow | 本仓库满足（`epic-gamer.yml` 在 master） |
| 免费服务单人运营 | cron-job.org 是长期个人项目 | 保留 GitHub `schedule` 作冗余，不删 |
| 60 天无活动自动停用 | public 仓库 60 天无提交，scheduled workflow 会被 GitHub 停用 | 外部 dispatch 不受此限；也可每月一次空提交 |

---

## 6. 15 分钟速配清单

- [ ] 建 fine-grained PAT：仅 `qq4756283/epic-freebies-helper`，Actions: Read and write，90 天
- [ ] cron-job.org 注册
- [ ] 任务 1：周四 17:00 UTC，POST dispatch API，204 测试通过
- [ ] 任务 2：周五 17:00 UTC，同上
- [ ] 两个任务都开失败邮件通知
- [ ] （可选）UptimeRobot heartbeat 1 天 / 宽限 2 天 + 成功步骤 ping
- [ ] 确认 GitHub `schedule` 仍保留（冗余，不要删）
- [ ] 一周后检查：`docs/scoreboard.json` 是否至少有一条本周记录

---

## 附：为什么不建议的替代方案

- **self-hosted runner**：不解决调度丢触发（触发源仍是 GitHub），且 public 仓库挂自托管
  runner 有安全风险（官方明确不建议）——除非把仓库转 private。
- **免费代理**：学术实测 92%+ 不可用、5–38% 存在内容注入/TLS 拦截，对 Epic 凭据是直接威胁。
- **Oracle Always Free**：仍免费但 2026-06 起减半为 2 OCPU/12GB、需绑卡、有无预警封号史，
  且其数据中心 IP 对 Epic 风控的收益**没有任何实测证据**，只作备选。
