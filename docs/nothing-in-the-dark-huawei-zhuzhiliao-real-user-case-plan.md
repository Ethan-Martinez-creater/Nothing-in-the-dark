# Nothing-in-the-dark 真实用户场景端到端案例测试方案

> 案例主题：华为“竹知了”事件  
> 正式调查时间窗口：2026-08-10 00:00:00 ～ 2026-08-20 23:59:59（Asia/Shanghai）  
> 案例性质：真实公开事件、真实采集、真实 Investigation 工作流模拟  
> 测试目的：尽可能覆盖 Nothing-in-the-dark 当前主要产品功能，同时让使用者通过一次完整调查理解系统运转逻辑  
> 面向对象：负责实际启动系统、按真实用户方式操作、记录结果并生成案例报告的执行智能体  
> 仓库：`Ethan-Martinez-creater/Nothing-in-the-dark`  
> 编写时参考 HEAD：`6eb779869ee1c71a465f27a3af90041d7e5d049d`
>
> 本文是**案例测试执行规格**，不是产品优化计划。执行过程中发现问题应记录，不应直接修改产品代码。

---

# 1. 案例定位

本案例模拟一个真实用户在事件已经经历初期爆发后，使用 Nothing-in-the-dark 对指定时间段进行系统性复盘调查。

用户身份设定：

> 舆情 / 网络信息调查分析员。

用户收到任务：

> 对 2026 年 8 月 10 日至 8 月 20 日期间“华为竹知了事件”的网络讨论进行复盘，识别该阶段主要叙事、传播平台、观点分化和二次归因，核查高风险说法的证据基础，形成经过人工审核、可追溯并可正式发布的调查报告；完成调查后继续建立监测规则，观察事件是否仍有长尾传播。

本案例不是：

```text
准备一批固定数据
→ 看页面能不能显示
```

而是：

```text
用户创建 Investigation
→ 定义 Collection Scope
→ 使用系统真实 Crawler 采集
→ 检查 Live Data
→ 让 Agent 基于已采集数据调查
→ Evidence / Timeline / Network
→ Findings
→ Human Review
→ Report
→ Citation Gate
→ Publish
→ Monitor / Signal
→ Activity / Provenance / Run Trace
```

---

# 2. 为什么选择 2026-08-10 ～ 2026-08-20

这个时间段适合验证系统的“调查能力”，而不仅是热点发现能力。

公开资料表明：

- “竹知了”讨论在 7 月下旬开始扩散；
- 8 月 4 日晚鸿蒙智行公开说明称，当时累计监测到相关网络信息 5.6 万余条，对其中 171 条具体内容发起投诉，平台反馈下架 144 条，并否认“投诉竹知了商品本身”“要求电商下架停售”等说法；
- 多份舆情复盘将 8 月 4–5 日视为主要声量高峰；
- 8 月 10 日以后，讨论进入复盘、责任归因和二次叙事阶段；
- “是否存在有组织操控”“是否属于黑公关”“是否只是网络玩梗”“企业维权边界”“史翠珊效应”等互相冲突的框架继续传播；
- 到 8 月 18 日左右，“竹知了”已经在部分与华为相关的其它讨论中成为一种长尾网络符号。

因此本案例的核心不是重新回答：

```text
“竹知了是什么？”
```

而是调查：

```text
8月10日至20日，
事件在高峰之后如何继续传播？
哪些叙事在延续？
哪些说法有事实证据？
哪些属于观点或推断？
舆论是否出现新的归因框架？
事件是否已经进入长尾符号化阶段？
```

---

# 3. 本案例最重要的产品测试目标

本案例必须尽可能覆盖当前主要用户功能。

## 3.1 调查入口与范围管理

```text
Home
Investigations
Case 创建
Investigation Overview
Goal / Plan
Collection Definition
Collection Version
Active Collection
```

## 3.2 真实数据采集

```text
Crawler
Crawl Approval
Collection Definition → Crawl
Multi-platform Crawl
Time Range
Keywords
Exclusions
Coverage
SourcePost Persistence
Comments
Live Data
Media
Platform Comparison
```

## 3.3 Agent Harness

```text
User Message
Coordinator
Expert Dispatch
Tool Calls
Model Calls
Durable Run
SSE
Approval / Resume
Artifact
Run Trace
Activity
```

## 3.4 调查工作区

```text
Evidence
Claims
Semantics
Timeline
Propagation Network
Alignment
Integrity
Findings
Provenance
Contextual Copilot
```

## 3.5 Human Governance

```text
Finding candidate
Submit Review
ReviewItem
Claim
Decision
current_version
expected_version
Verified / Rejected
Reopen（建议）
```

## 3.6 报告闭环

```text
Report Agent
Report Artifact
ReportDocument
Draft
Citation Gate
Publish
Revision
Global Reports
Download
```

## 3.7 持续监测

```text
Monitor
Alert Rule
Execution
Signal
Acknowledge
Resolve
State Machine
```

---

# 4. 测试原则

## 4.1 本案例必须启用真实采集模块

与此前受控 fixture 方案不同，本案例：

> **禁止将预先准备的 Social Posts 直接写入数据库作为主数据源。**

正式 Investigation 数据必须来自当前系统真实：

```text
Collection Definition
→ crawl tool
→ SocialCrawlerPort
→ MediaCrawlerAdapter
→ SocialRepository
```

当前系统 MediaCrawler Adapter 支持：

```text
weibo
bilibili
tieba
zhihu
douyin
```

并会在采集结果归一化后依据 Investigation 的 `time_range` 进行时间窗口过滤。

---

## 4.2 不要求每个平台一定成功

真实用户使用时可能遇到：

```text
登录状态过期
Cookie 缺失
二维码登录
平台反爬
页面结构变化
搜索结果为空
```

因此：

```text
某个平台环境失败
≠ 整个系统自动 FAIL
```

但必须记录：

```text
平台
错误
是否环境问题
是否影响主要调查结论
```

如果所有平台都无法采集：

```text
Acquisition = BLOCKED
Overall = PARTIAL / FAIL
```

不得偷偷改成 fixture 数据继续宣称“真实采集通过”。

---

## 4.3 正式观察窗口固定

所有用于窗口统计和主要 Investigation 结论的数据必须满足：

```text
2026-08-10 00:00:00+08:00
≤ published_at
≤
2026-08-20 23:59:59+08:00
```

禁止为了获得更多数据私自把正式窗口改成 8 月 1 日开始。

---

## 4.4 背景资料与窗口数据必须分开

8 月 4 日鸿蒙智行声明等内容是理解事件的重要背景，但不属于本案例正式统计窗口。

如果系统或 Agent 找到早于 8 月 10 日的背景材料，应标记：

```text
BACKGROUND / PRE-WINDOW CONTEXT
```

不得加入：

```text
8/10–8/20 volume
platform distribution
window trend
```

统计。

---

## 4.5 不设置“政治立场式 Oracle”

这是现实争议事件。

测试不要求 Agent 得出：

```text
“华为一定正确”
```

或：

```text
“网友一定正确”
```

也不要求系统认定：

```text
“一定存在幕后黑手”
```

真正要验证的是：

> 系统能否区分事实、公开声明、媒体观点、用户调侃、指控、推断和证据不足的说法。

---

# 5. 已知背景事实锚点

以下只作为评审最终输出时的外部参照，**不要直接整段放进 Agent Prompt**。

## B1

鸿蒙智行在 8 月 4 日公开说明中表示：

```text
截至 8 月 4 日晚监测到相关信息 5.6 万余条；
对其中 171 条具体侵权内容发起投诉；
平台反馈下架 144 条。
```

## B2

该说明还表示：

```text
投诉针对具体侵权内容，
不是针对“竹知了”玩具本身；
未要求电商平台下架或停售竹知了商品。
```

这些属于：

```text
官方公开声明
```

不是执行智能体可以自行升级为“独立第三方已经证明”的事实。

## B3

公开舆情复盘认为主要爆发高峰早于本案例窗口，大约在：

```text
8 月 4–5 日
```

因此 8 月 10–20 日主要观察：

```text
后续复盘
二次归因
长尾传播
符号化使用
```

## B4

“存在幕后黑手 / 有组织操控 / 黑公关流水线”等说法确实曾在公开讨论中出现。

但是：

```text
“这些说法存在”
```

和：

```text
“这些说法已经被证实”
```

是两件不同的事。

这将是本案例 Evidence / Finding / Review 的核心测试点之一。

---

# 6. 用户最终希望回答的调查问题

## Q1 — 窗口内事件仍有多大讨论度？

```text
2026-08-10 至 2026-08-20，
“竹知了”相关舆论是否仍保持明显传播？
```

系统应基于实际采集数据回答。

---

## Q2 — 主要平台是什么？

```text
微博 / 抖音 / B站 / 知乎 / 贴吧
各平台在窗口内承担什么角色？
```

可能包括：

```text
热点转发
评论争论
长视频复盘
事实讨论
玩梗长尾
```

不能预先假设结论。

---

## Q3 — 窗口内主要叙事有哪些？

重点观察至少以下候选 Narrative：

```text
N1 企业维权边界 / 过度投诉
N2 普通玩梗与表达空间
N3 “全网下架 / 禁售竹知了”叙事
N4 官方澄清与事实纠偏
N5 “有组织攻击 / 黑公关 / 幕后操控”叙事
N6 史翠珊效应 / 公关反噬
N7 竹知了成为华为相关讨论中的网络符号
```

这些是**搜索和分类候选**，不是预设结论。

---

## Q4 — “华为要求全网停售竹知了”在窗口内是否仍传播？

调查：

```text
这种说法是否仍出现？
来源是什么？
有没有反证或官方澄清？
```

预期系统应至少能够区分：

```text
用户声称
vs
官方公开说明
```

---

## Q5 — “普通展示竹知了也被大规模下架”是否有充分证据？

要求 Evidence Agent：

```text
列出具体 source
区分个案与整体
标记无法确认的部分
```

不得把单条用户经历自动外推为：

```text
所有普通视频都被处理
```

---

## Q6 — “事件是有组织黑公关操控”的证据有多强？

这是本案例最关键的 Epistemic Test。

要求系统区分：

```text
A. 有人在公开讨论中提出该指控
B. 某些账号内容/时间模式具有相似性
C. 有独立可验证证据证明组织关系
```

只有 C 足够时才能产生高置信度事实 Finding。

如果只有 A/B：

```text
Finding 应保持 candidate / insufficient / disputed
```

Human Review 不应轻易 verified。

---

## Q7 — 事件在 8 月 10–20 日是否出现“长尾符号化”？

例如：

```text
在其它华为话题下仍使用
“竹知了”
“哇哇哇”
“一千万以内……”
等作为隐喻或调侃标签。
```

需要 Agent 基于窗口内真实数据判断。

---

## Q8 — 在窗口内有哪些关键转折？

Timeline 应帮助回答：

```text
哪一天讨论结构发生变化？
```

例如：

```text
从事件事实争论
→ 责任归因
→ 媒体复盘
→ 玩梗长尾
```

以实际数据为准。

---

# 7. Preflight — 真实用户启动前检查

执行智能体必须记录：

```text
HEAD
git status
Backend port
Frontend port
DATABASE_URL
DEMO_MODE
Crawler backend
MediaCrawler root
MediaCrawler login type
LLM provider
Reasoning model
Report model
Embedding/Sentiment worker 状态
```

---

# 8. 数据库原则

建议使用：

```text
专门案例数据库
```

例如：

```env
DATABASE_URL=sqlite+aiosqlite:///./data/huawei_zhuzhiliao_case.db
```

如果用户当前已有正常开发数据库且明确允许测试，可使用现有数据库创建独立 Case。

但禁止：

```text
清空用户现有业务数据库
```

---

# 9. Crawler Preflight

执行前确认 MediaCrawler：

```text
main.py 存在
Python executable 正确
usage_mode = research
output retention 未满
```

检查 5 平台认证状态：

```text
weibo
douyin
bilibili
zhihu
tieba
```

推荐先不要为了本案例重新配置所有平台。

按当前已经可工作的认证状态执行。

---

# 10. Phase 1 — 用户从 Home 开始

真实用户流程从：

```text
/
```

开始。

检查：

```text
[ ] Home 正常
[ ] Investigations 可进入
[ ] Signals 可进入
[ ] Reports 可进入
[ ] Administration 可进入
```

记录 Home 是否已有：

```text
operational summary
recent investigations
signals
```

---

# 11. Phase 2 — 创建 Investigation

用户创建：

```yaml
title: 华为“竹知了”事件 8月10日至20日舆情复盘
topic: 华为 竹知了 余承东 鸿蒙智行
description: >
  对2026年8月10日至20日期间围绕“华为竹知了事件”的公开网络讨论进行复盘，
  分析主要传播平台、叙事演变、事实争议、责任归因以及事件的长尾符号化。
  特别关注“全网下架/禁售”“普通玩具视频被投诉”
  “有组织黑公关/幕后操控”等高争议说法的证据基础，
  并区分事实、公开声明、观点和推断。
platforms:
  - weibo
  - douyin
  - bilibili
  - zhihu
  - tieba
time_start: 2026-08-10T00:00:00+08:00
time_end: 2026-08-20T23:59:59+08:00
```

记录：

```text
CASE_ID
```

---

# 12. Investigation 创建验收

```text
[ ] Investigation 列表出现
[ ] Overview 打开
[ ] 标题正确
[ ] 5个平台正确
[ ] 时间范围正确
[ ] Activity 有创建记录（若当前设计记录）
```

---

# 13. Phase 3 — Goal / Plan

按照真实分析员工作习惯，在 Overview 的 Plan / Goal 中明确调查目标。

建议：

```text
Goal 1：确认窗口内主要舆情叙事。
Goal 2：识别关键传播节点和平台角色。
Goal 3：核查“全网下架/禁售”等事实性说法。
Goal 4：评估“组织操控/黑公关”指控的证据强度。
Goal 5：形成经过 Human Review 的 Findings。
Goal 6：生成正式调查报告并建立后续 Monitor。
```

验证：

```text
[ ] Goal 保存
[ ] Overview 可见
```

---

# 14. Phase 4 — Collection Definition V1

真实用户首先创建较宽的采集定义。

推荐 V1：

```json
{
  "goal": "收集2026年8月10日至20日华为竹知了事件的公开讨论，用于复盘主要叙事、观点分化、传播路径和争议性事实。",
  "platforms": [
    "weibo",
    "douyin",
    "bilibili",
    "zhihu",
    "tieba"
  ],
  "platform_queries": {
    "weibo": [
      "竹知了",
      "华为 竹知了",
      "余承东 竹知了",
      "鸿蒙智行 竹知了",
      "起底竹知了事件背后黑手"
    ],
    "douyin": [
      "竹知了",
      "华为 竹知了",
      "余承东 竹知了"
    ],
    "bilibili": [
      "竹知了",
      "华为 竹知了",
      "竹知了事件复盘"
    ],
    "zhihu": [
      "竹知了 华为",
      "竹知了事件",
      "华为投诉竹知了"
    ],
    "tieba": [
      "竹知了",
      "华为 竹知了"
    ]
  },
  "exclusions": [
    "知了猴养殖",
    "蝉养殖",
    "竹知了制作教程"
  ],
  "filters": {}
}
```

激活：

```text
V1 = active
```

---

# 15. 为什么 V1 不能一开始过度精确

真实用户通常不知道事件窗口内到底有哪些 Narrative。

如果一开始只搜：

```text
“幕后黑手”
```

会造成 selection bias。

因此 V1 先获取：

```text
广覆盖 baseline
```

然后再依据 Live Data 创建 V2。

---

# 16. Phase 5 — 第一次真实采集

用户不直接调用数据库。

真实使用方式优先：

```text
通过 Investigation Copilot 发出采集请求
```

Prompt：

> 请按照当前 Investigation 已激活的 Collection Definition，采集 2026 年 8 月 10 日至 8 月 20 日关于“华为竹知了事件”的公开讨论。先执行数据采集，不要立即生成最终结论。采集完成后总结各平台覆盖情况、有效帖子数量、时间分布和可能遗漏。

---

# 17. Crawl Approval 必须真实出现

如果当前 crawl tool 需要审批：

```text
Run interrupted
→ Approval UI
```

用户：

```text
检查平台
检查关键词
检查时间范围
→ Approve
```

验证：

```text
[ ] approval 可见
[ ] approve 后同一个 Run resume
[ ] 不创建重复 Parent Run
```

这是 Harness 的核心真实用户体验。

---

# 18. Crawl Run 验收

记录：

```text
CRAWL_RUN_ID
```

检查：

```text
pending
→ running / awaiting approval
→ running
→ completed
```

并检查：

```text
Tool Call = crawl
```

---

# 19. 平台采集验收

为每个平台记录：

| Platform | Result | Raw hits | In-window | Error |
|---|---:|---:|---:|---|
| Weibo | | | | |
| Douyin | | | | |
| Bilibili | | | | |
| Zhihu | | | | |
| Tieba | | | | |

真实数字由执行结果填写。

---

# 20. 时间窗口验收

随机抽样：

```text
≥ 10 条 Source Posts
```

或全部（不足 10 时全部）。

检查：

```text
published_at
```

必须在：

```text
8/10–8/20
```

正式 Live Data 中如果出现窗口外帖子：

```text
DEFECT
```

除非产品明确标成 Background。

---

# 21. Collection Exclusion 验收

检索是否出现明显：

```text
知了猴养殖
蝉养殖
竹制玩具制作教程
```

噪声。

理想：

```text
被 exclusions 过滤
```

如果仍有：

```text
记录具体 SourcePost
```

---

# 22. Phase 6 — Live Data 初检

用户进入：

```text
/investigations/{CASE_ID}/live-data
```

按真实用户习惯检查：

```text
Posts
Media
Platform Comparison
```

---

# 23. Posts 验收

测试：

```text
[ ] Pagination / load-more
[ ] 搜索“竹知了”
[ ] 搜索“幕后”
[ ] platform filter
[ ] time filter
[ ] 打开 source URL
[ ] 点击 Post
[ ] Copilot context 切换到该 Post
```

---

# 24. Media 验收

如果采集结果包含：

```text
image
video cover
media URL
```

进入 Media：

```text
[ ] Media assets 可见
[ ] source post 可追溯
```

若 OCR / media extraction worker 已配置：

```text
执行至少一个真实 media analysis
```

否则：

```text
ENVIRONMENT NOT CONFIGURED
```

不作为核心失败。

---

# 25. Platform Comparison

观察：

```text
各平台采集量
互动指标
内容类型
```

不要求不同平台样本量完全平衡。

记录：

> 实际 Crawler 搜索偏差会影响平台占比，因此平台比较应理解为“本次 Collection 的观测样本”，不是全网总体份额。

---

# 26. Phase 7 — 用户基于数据修订 Collection

这是非常重要的真实用户步骤。

先人工浏览 20–50 条内容，识别窗口内实际出现的关键词。

如果发现以下 Narrative：

```text
幕后黑手
组织操控
黑公关
史翠珊效应
全网下架
禁止销售
投诉视频
玩梗
公关危机
```

创建：

```text
Collection V2
```

而不是修改 V1。

---

# 27. Collection V2 建议

在 V1 上新增：

```text
华为 竹知了 黑公关
竹知了 幕后黑手
竹知了 组织操控
竹知了 史翠珊效应
竹知了 全网下架
竹知了 禁止销售
竹知了 公关危机
竹知了 玩梗
```

激活 V2。

验证：

```text
[ ] V1 = superseded
[ ] V2 = active
[ ] version 增加
[ ] 历史 V1 可查看
```

---

# 28. Phase 8 — 第二次补充采集

用户 Prompt：

> 我已经根据第一轮数据更新了 Collection Definition。请使用当前 Active Definition 补充采集同一时间窗口的数据，重点补足“幕后操控/黑公关”“全网下架/禁售”“企业维权边界”“史翠珊效应”等叙事，但不要删除第一轮已经采集的数据。完成后报告新增覆盖和重复去重情况。

验证：

```text
[ ] Crawl 使用 V2
[ ] collection_definition id/version 出现在 trace/audit
[ ] 新数据进入同一 Case
[ ] 重复 native_id 不造成明显重复
```

---

# 29. Acquisition Gate

进入 Agent 深度分析之前，至少满足：

```text
≥ 2 个平台成功采集
AND
≥ 20 条窗口内有效 Source Posts
```

推荐：

```text
≥ 50
```

但真实平台环境可能限制。

如果：

```text
< 20
```

执行智能体不得伪造。

应：

```text
尝试一次 Collection Query 调整
```

仍不足则：

```text
DATA COVERAGE = LOW
```

继续调查但报告限制。

---

# 30. Phase 9 — Timeline 先于 Agent 结论

用户先自己查看：

```text
Timeline
```

真实调查员通常会先观察数据形态，再问 Agent。

检查：

```text
Volume
Platform
Narrative
```

---

# 31. Timeline 用户操作

选择：

```text
8/10–8/12
```

观察早期窗口。

再选择：

```text
8/17–8/20
```

观察尾部窗口。

验证：

```text
Copilot ui_context.time_range
```

同步变化。

---

# 32. Phase 10 — 第一次深度 Agent Investigation

Prompt：

> 基于当前 Case 在 2026 年 8 月 10 日至 8 月 20 日已经采集并持久化的数据，对“华为竹知了事件”进行窗口内舆情复盘。  
> 
> 请完成：
> 1. 识别窗口内主要 Narrative，并给出每类 Narrative 的代表性来源；
> 2. 分析各平台在讨论中的角色差异；
> 3. 找出窗口内关键时间节点与传播变化；
> 4. 核查“华为要求全网下架/停售竹知了”“普通展示玩具也会被大规模投诉”这类事实性说法；
> 5. 单独分析“幕后黑手/有组织操控/黑公关”指控，严格区分公开指控、行为模式线索和能够证明组织关系的证据；
> 6. 分析是否存在事件的长尾符号化，例如“竹知了”“哇哇哇”等在其它华为话题中的继续使用；
> 7. 所有重要事实判断必须关联当前 Case 中的真实 Evidence；证据不足时明确写“不足”，不得补全不存在的事实。
> 
> 本轮先完成调查分析，不生成最终正式报告。

---

# 33. Agent Run 必须记录

```text
PARENT_RUN_ID
Child Expert Run IDs
Tool Calls
Model Calls
Artifacts
```

---

# 34. Harness 验收

检查：

```text
[ ] Coordinator 实际工作
[ ] Expert Dispatch 存在（若任务需要）
[ ] child_run.parent_run_id 正确
[ ] Artifact 归属于对应 Run
[ ] Run 最终不残留 running
[ ] Trace 可打开
```

---

# 35. 预期 Expert 能力

不强制具体 Agent 数量。

但应覆盖语义：

```text
Opinion / Narrative
Fact Check
Propagation
Evidence
```

如果系统只调用一个 Expert 但能够完成要求：

```text
记录真实 Coordinator 选择
```

不要为了“测试 Agent 数量”强迫系统调用无关 Agent。

---

# 36. Phase 11 — Evidence Workspace

用户进入：

```text
Evidence
```

检查：

```text
Claims
Evidence
Semantics
Unassigned
```

---

# 37. 核心 Claim 候选

系统可能生成以下 Claim，具体以实际为准：

```text
C1 华为要求全网下架/禁售竹知了。
C2 普通展示竹知了的视频会被系统性投诉。
C3 鸿蒙智行只投诉具体侵权内容，不针对玩具本身。
C4 竹知了事件存在有组织网络操控。
C5 8月10–20事件进入长尾符号化传播。
C6 舆论讨论从事实争议转向责任归因/公关复盘。
```

---

# 38. Claim / Evidence 质量要求

对于每个 Claim：

```text
support
oppose
context
```

必须来自真实 Source 数据或正式 Artifact。

不能：

```text
LLM statement
→ 直接当 Evidence
```

---

# 39. 关键事实性测试：禁售/全网下架

如果 Case 数据中出现：

```text
“华为禁止销售竹知了”
“华为让电商全网下架竹知了”
```

Agent 必须把它作为：

```text
Claim
```

而不是自动作为事实。

检查是否找到：

```text
相反 Evidence
官方澄清引用
媒体转述
```

如果窗口内没有原始官方内容：

```text
必须标记依赖窗口外背景 / 二手转述
```

不能伪造 8/10–20 官方声明。

---

# 40. 关键 Epistemic Test：幕后黑手

如果 Agent 产出：

```text
“事件由某组织/竞争对手操控”
```

检查 Evidence。

以下不够：

```text
多个账号发布时间接近
图片相似
水印不同
文案类似
某博主声称有幕后黑手
```

这些最多属于：

```text
indicator / allegation / hypothesis
```

如果没有：

```text
明确组织关系
资金/委托证据
平台认证调查结果
权威执法结论
其它可验证链路
```

Human Review 不应将其 verified 为事实。

---

# 41. Phase 12 — Copilot Evidence Context

用户选择一条涉及：

```text
“有组织操控”
```

的 Evidence。

问：

> 这条证据能够证明什么？不能证明什么？请不要使用当前 Case 中没有的外部事实。

检查：

```text
workspace=evidence
selected_type=evidence
selected_id=<真实ID>
```

---

# 42. Phase 13 — Propagation Network

进入：

```text
Network → Propagation
```

检查：

```text
[ ] nodes 来自真实 Source Posts
[ ] edges 非伪造
[ ] confidence
[ ] relation
[ ] evidence IDs
[ ] inferred / reviewed 状态
```

---

# 43. Network 用户任务

用户尝试回答：

```text
窗口内有哪些高影响传播节点？
哪类 Narrative 在不同平台间迁移？
```

选择至少：

```text
1 个 node
1 个 edge
```

分别问 Copilot。

验证 Selection Context。

---

# 44. 人工确认一条传播关系

选择：

```text
证据充分的 edge
```

执行：

```text
unreviewed → confirmed
```

刷新确认。

---

# 45. 人工驳回一条弱传播关系

如果图中存在：

```text
仅基于文本相似度推断
但没有直接传播证据
```

用户：

```text
reject
```

如果没有合理可拒绝 edge：

```text
不要为了测试强行驳回正确边
```

记录：

```text
NOT APPLICABLE
```

---

# 46. Alignment / Integrity

如果当前 Network 页面：

```text
Alignment
Integrity
```

有数据：

至少打开并验证：

```text
[ ] 页面加载
[ ] 与当前 Case scope 一致
```

如 Agent 没产生对应对象：

```text
EMPTY / NOT PRODUCED
```

不造数据。

---

# 47. Phase 14 — Findings Materialization

进入：

```text
Findings
```

如果当前设计需要：

```text
Artifact → findings:sync
```

通过正式操作触发。

记录：

```text
FINDING_IDs
```

---

# 48. 预期 Finding 分类

理想系统应形成三种类型。

## A — 可验证事实

例如：

```text
窗口内“全网下架/禁售”说法仍有传播。
```

注意这是：

```text
“该说法在传播”
```

而不是：

```text
“禁售本身为真”
```

## B — 调查结论

例如：

```text
公开材料对“禁售玩具本身”的说法存在反证。
```

## C — 假设 / 高风险归因

例如：

```text
事件存在统一组织操控。
```

如果证据不足：

```text
不得直接 verified
```

---

# 49. Phase 15 — Human Review

至少选择：

```text
2 个 Findings
```

进行人工审核。

建议：

```text
F1 一个证据充分的事实 Finding
F2 一个“组织操控”类高风险 Finding（如果系统产生）
```

---

# 50. Review Flow

真实 UI：

```text
candidate
→ submit review
→ Review Workbench
→ claim
→ decide
```

记录：

```text
ReviewItem ID
current_version
expected_version
ReviewDecision ID
Finding final status
```

---

# 51. Review Version 正常验证

不做并发压力。

只检查：

```text
claim 后 version 增长
decision 请求带 expected_version
decision 后 version 再增长
```

证明最近的并发硬化已进入真实场景。

---

# 52. 高风险 Finding 审核标准

对于：

```text
“某组织操控了竹知了事件”
```

如果没有强证据：

推荐人工：

```text
rejected
```

或保留非 verified 状态。

审核理由示例：

> 当前证据能够证明“网络上存在有组织操控的指控”和部分传播模式相似性，但不足以独立证明具体组织关系、委托关系或幕后主体，不宜作为事实性结论发布。

---

# 53. 可选：Reopen

对已接受的普通 Finding：

```text
accepted / verified
→ reopen
→ in_review / under_review
→ 再次 approve
```

用于理解审核生命周期。

不是必须。

---

# 54. Phase 16 — Provenance

分别查看：

```text
一个 verified Finding
一个 rejected Finding（若有）
```

检查 upstream：

```text
Evidence
Artifact
```

报告生成后再检查 downstream。

---

# 55. Phase 17 — 第二次 Agent：生成 Report

只有 Human Review 完成后。

Prompt：

> 请根据当前 Investigation 中已经采集的数据、Evidence、人工审核结果以及 verified Findings，生成一份针对 2026 年 8 月 10 日至 20 日“华为竹知了事件”的正式舆情复盘报告。
>
> 报告必须：
> 1. 明确调查窗口；
> 2. 将窗口外背景单独标记，不能混入窗口统计；
> 3. 总结主要 Narrative 与平台差异；
> 4. 描述窗口内传播阶段；
> 5. 对“全网下架/禁售”“普通内容被大规模投诉”等事实性说法区分证据强度；
> 6. 对“幕后黑手/组织操控/黑公关”等高风险归因只引用经过审核的结论；
> 7. 对证据不足的内容明确写限制；
> 8. 所有核心结论必须引用系统内真实 Evidence / Finding / Artifact ID；
> 9. 不得生成不存在的 citation。

---

# 56. Report Artifact 验收

记录：

```text
REPORT_RUN_ID
REPORT_ARTIFACT_ID
```

检查：

```text
title
executive summary
window
method
narratives
timeline
platform analysis
findings
limitations
citation_links
```

---

# 57. Import ReportDocument

通过当前真实产品路径：

```text
Report Artifact
→ ReportDocument draft
```

记录：

```text
REPORT_DOCUMENT_ID
```

---

# 58. 用户编辑 Report

真实用户通常不会直接发布 Agent 第一稿。

要求执行智能体模拟用户：

```text
阅读 draft
修改至少一处措辞
保存
```

建议修改：

```text
把“证明”改成“现有证据支持”
```

如果 Agent 表述过强。

验证：

```text
optimistic version
draft persistence
```

---

# 59. Citation Gate 正路径

发布前检查：

```text
所有 citation 存在
同 Case
Finding/Evidence/Artifact 合法
```

然后：

```text
Publish
```

预期：

```text
published
```

---

# 60. Citation Gate 负路径技术探针

为了覆盖系统完整性保护，正式报告发布之后可以创建一个：

```text
TEST DRAFT
```

故意将一个 citation 改成不存在 ID。

尝试 publish。

必须：

```text
blocked
```

不得修改正式报告。

完成后归档该测试 draft。

---

# 61. Report Revision

对已经 published 的正式报告：

```text
Create Revision
```

验证：

```text
published 原版本不可直接被改写
new draft version 创建
```

不要求再次发布 revision。

这覆盖 Report lifecycle。

---

# 62. Global Reports

进入：

```text
/reports
```

验证：

```text
正式报告可见
Case linkage
status
download
```

下载 HTML。

---

# 63. 最终 Report 质量检查

检查不能出现：

```text
把官方声明自动当成独立事实
把媒体观点自动当事实
把“有人指控幕后黑手”写成“幕后黑手已证实”
把窗口外内容统计到8/10–20
不存在 citation
```

---

# 64. Phase 18 — Activity

进入：

```text
Activity
```

用户回顾整个 Case。

至少应看到：

```text
Collection 相关操作
Agent Run
Finding Review
Report lifecycle
```

具体以当前 Activity coverage 为准。

---

# 65. Activity 与 Timeline 区别

结果报告必须解释：

```text
Timeline
= 被调查事件的社会传播时间

Activity
= 用户与系统执行调查工作的业务时间
```

---

# 66. Phase 19 — Run / Trace 教学检查

打开：

```text
第一次调查 Run
第二次 Report Run
```

检查：

```text
Parent Run
Child Runs
Tool Calls
Model Calls
Artifacts
Events
```

绘制真实对象链：

```text
User Turn
→ Parent Run
→ Expert Run
→ Tool / Model
→ Artifact
```

---

# 67. Phase 20 — Monitor：调查结束后的持续观察

这是非常符合真实用户习惯的一步。

用户完成 8/10–20 的历史调查后，不应继续把 Investigation Window 改到现在。

而是新建：

```text
Monitor
```

用于从当前时间继续监控竹知了长尾。

---

# 68. Monitor Definition

建议：

```yaml
name: 华为竹知了事件长尾监测
keywords:
  - 竹知了
  - 华为 竹知了
  - 余承东 竹知了
  - 鸿蒙智行 竹知了
  - 起底竹知了事件背后黑手
platforms:
  - weibo
  - douyin
  - bilibili
  - zhihu
  - tieba
schedule: hourly / reasonable supported interval
```

---

# 69. Monitor 与 Investigation 时间区别

必须保持：

```text
Investigation:
8/10–8/20 retrospective

Monitor:
当前及未来 continuous
```

不能为了测试 Monitor：

```text
把 Investigation time window 改成今天
```

---

# 70. Alert Rule

为了在真实环境尽量产生一次 Signal，可设置较低但合理阈值。

例如：

```text
absolute post volume >= 1
```

或当前 Monitor 支持的最低有效阈值。

目的：

```text
测试 Signal workflow
```

不是做真实生产告警参数调优。

---

# 71. Run Monitor Now

执行：

```text
run-now
```

记录：

```text
MONITOR_ID
EXECUTION_ID
```

---

# 72. Monitor 结果

如果当前搜索仍有：

```text
竹知了
```

相关内容：

应得到：

```text
execution
possibly alert
```

如果没有命中：

```text
Monitor Execution PASS
Alert = NOT TRIGGERED
```

不能 seed Alert。

---

# 73. Signals

如果真实 Alert 产生：

```text
open
→ acknowledge
→ resolve
```

测试 UI。

然后技术负路径：

```text
resolved → acknowledged
```

必须被拒绝。

---

# 74. 如果没有真实 Alert

Signal 状态流测试标：

```text
NOT EXECUTED — RULE NOT TRIGGERED
```

这不代表 Signal 模块失败。

如果希望强制测试 Signal：

可以在**单独技术测试 Case**中使用系统已有自动化测试方式，不应污染本真实案例。

---

# 75. Phase 21 — Administration

本案例只做轻量验证。

进入：

```text
Administration
```

检查：

```text
Notifications
Review Workbench
其它当前管理入口
```

不要求实际发送 Webhook。

---

# 76. Optional Notification

如果用户已经配置测试 Webhook：

可以让 Monitor Alert 触发一次 Delivery。

否则：

```text
NOT CONFIGURED
```

---

# 77. Contextual Copilot 全工作区测试

至少在以下位置各提一个真实问题：

## Live Data

选中一条 Post：

> 这条内容属于哪个主要 Narrative？

## Timeline

选择一个时间范围：

> 这个时间段与前一个阶段相比有什么变化？

## Evidence

选一个 Evidence：

> 它能证明什么，不能证明什么？

## Network

选一个 edge：

> 这条传播关系为什么被推断出来？

## Findings

选一个 Finding：

> 这个结论依赖哪些证据？还缺什么？

记录每次：

```text
workspace
selected_type
selected_id
time_range
```

---

# 78. Contextual Copilot 关键验收

Copilot 不能把：

```text
UI Context
```

当成事实证据。

例如用户选中：

```text
“幕后黑手”相关 Post
```

Copilot 不能因为当前选择就回答：

```text
“幕后黑手已经存在”
```

应继续查 Evidence。

---

# 79. 用户真实使用中的中途修正

真实用户可能发现 Agent 分析偏离。

案例要求至少模拟一次：

```text
用户追问 / 修正
```

例如：

> 你刚才把“有人提出幕后操控指控”和“幕后操控已经被证明”混在一起了。请重新整理，只保留能够被当前 Evidence 支撑的结论。

检查新 Run / Turn 是否正确进入历史。

---

# 80. Chat / Copilot History

关闭并重新打开 Investigation。

检查：

```text
之前 User Turns
Agent Responses
Expert Runs
Artifacts
```

仍能正确重建。

这是用户长期使用体验的重要部分。

---

# 81. 调查结果不设置固定答案

执行智能体不得为了让测试 PASS，要求系统得出某个预写政治/商业结论。

本案例最终判断重点是：

```text
Evidence discipline
Uncertainty
Case scope
Traceability
Human Review
Citation integrity
```

---

# 82. 外部参考比较

系统完成正式报告后，执行智能体可以使用公开资料对结果做“外部合理性比较”。

建议对比：

```text
鸿蒙智行公开情况说明
8月10日前后舆情复盘材料
8月11媒体复盘
8月中旬长尾讨论
```

只比较：

```text
是否漏掉主要公开 Narrative
是否把争议观点误当事实
时间线是否明显错误
```

不要求与某一篇媒体文章观点一致。

---

# 83. 重点高风险错误

以下任一出现，至少 P1。

## E1

```text
“华为全网禁售竹知了”
```

无 Evidence 仍被 verified。

## E2

```text
“某竞争对手/组织是幕后黑手”
```

仅依据微博博主推断就被 verified。

## E3

窗口外数据混入窗口统计。

## E4

Agent 生成不存在 Citation。

## E5

Report Publish Gate 没阻止不存在 Citation。

## E6

Finding 绕过 Human Review 直接 verified。

## E7

Review 状态与 Finding 不一致。

## E8

Propagation edge 指向不存在 Post/Evidence。

## E9

跨 Case 对象泄漏。

---

# 84. 主要功能覆盖矩阵

| 功能 | 案例 Phase | 结果 |
|---|---|---|
| Home | 1 | |
| Investigation | 2 | |
| Goal/Plan | 3 | |
| Collection V1 | 4 | |
| Crawl | 5 | |
| Approval | 5 | |
| Live Data | 6 | |
| Media | 6 | |
| Platform Comparison | 6 | |
| Collection V2 | 7 | |
| Incremental Crawl | 8 | |
| Timeline | 9 | |
| Agent Coordinator | 10 | |
| Expert Run | 10 | |
| SSE / Trace | 10 | |
| Artifact | 10 | |
| Evidence | 11 | |
| Semantics | 11 | |
| Copilot Context | 12 / 21 | |
| Propagation | 13 | |
| Alignment / Integrity | 13 | |
| Findings | 14 | |
| Human Review | 15 | |
| Review Version | 15 | |
| Provenance | 16 | |
| Report Agent | 17 | |
| ReportDocument | 17 | |
| Citation Gate | 17 | |
| Publish | 17 | |
| Revision | 17 | |
| Global Reports | 17 | |
| Activity | 18 | |
| Monitor | 20 | |
| Alert Rule | 20 | |
| Signals | 20 | |
| Administration | 21 | |
| History Reconstruction | 21 | |

---

# 85. Core PASS / Conditional PASS

## Core 必须正常

```text
Investigation
Collection
至少2个平台真实 Crawl
Live Data
Agent Run
Artifact
Timeline
Evidence
Findings
Human Review
Report
Citation Gate
Publish
Provenance
Activity
Copilot Context
```

## 条件能力

```text
Media OCR
Alignment
Integrity
Monitor Alert
Webhook Delivery
```

如果没有数据/环境：

```text
NOT APPLICABLE / NOT CONFIGURED
```

不得伪造 PASS。

---

# 86. 数据覆盖最低要求

建议：

```text
至少2个平台
至少20条窗口内帖子
```

否则正式报告必须：

```text
LOW COVERAGE
```

并在 limitations 写明。

如果只有一个平台：

```text
Cross-platform conclusion 禁止高置信度
```

---

# 87. Agent 重试规则

允许最多：

```text
1 次针对明显遗漏的补充 Run
```

例如：

> 请补充传播网络分析。

禁止：

```text
无限重试
直到模型产生用户想要的观点
```

---

# 88. Defect 处理

执行中发现 Bug：

```text
记录
不修
```

Defect 模板：

```markdown
## DEFECT-XX

Severity:
P0/P1/P2/P3

Phase:

Observed:

Expected:

Steps:

IDs:
- Case
- Run
- Artifact
- Finding
- Review

Evidence:

Impact:

Blocker:
yes/no
```

---

# 89. P0 定义

例如：

```text
跨 Case 数据泄漏
Human Review 可绕过
Citation Gate 可绕过
数据库状态损坏
Crawler 将窗口外数据错误作为窗口内数据
```

---

# 90. P1 定义

例如：

```text
主要 Workspace 无法完成
Propagation 声称成功但没有 materialize
Agent 严重证据越界
Review/Report 核心链断裂
```

---

# 91. P2/P3

```text
P2 非阻塞功能问题
P3 文案 / UX / 轻微显示
```

---

# 92. 最终结果文档

执行智能体必须生成：

```text
docs/learning-case-huawei-zhuzhiliao-20260810-0820-result.md
```

---

# 93. 结果文档结构

## A. Executive Result

```text
Overall:
PASS / PASS WITH FINDINGS / PARTIAL / FAIL

Core journey:
...

Main defects:
...
```

## B. Environment

```text
HEAD
Database
Crawler
Platform login
LLM
Workers
Execution date
```

## C. Investigation

```text
CASE_ID
time window
Collection V1 ID
Collection V2 ID
```

## D. Acquisition

实际表：

| Platform | Status | Posts | Comments | Notes |
|---|---:|---:|---:|---|

## E. Timeline

真实观察结果。

## F. Narrative Map

实际 Agent 识别的 Narrative。

## G. Evidence / Claims

真实 IDs。

## H. Network

nodes / edges / human reviewed edges。

## I. Findings

| Finding | Status | Confidence | Review |
|---|---|---|---|

## J. Report

```text
Artifact
ReportDocument
Publish
Revision
```

## K. Monitor / Signal

真实结果。

## L. Defects

## M. System Walkthrough

---

# 94. System Walkthrough 必须使用真实 ID

示例格式：

```text
Investigation CASE-xxx
    ↓
Collection v1 COL-xxx
    ↓
Crawl Run RUN-xxx
    ↓
Source Posts N
    ↓
Collection v2 COL-yyy
    ↓
Analysis Run RUN-yyy
    ↓
Artifacts ART-...
    ↓
Evidence EV-...
    ↓
Finding FIND-...
    ↓
Review REV-...
    ↓
Report REP-...
    ↓
Monitor MON-...
```

---

# 95. 用户理解视角

结果文档每个阶段都要写：

```text
用户看到了什么？
用户做了什么？
后台真正发生了什么？
产生了什么对象？
为什么下一步需要这个对象？
```

---

# 96. 示例：Collection 的教学解释

不要只写：

```text
Collection API PASS
```

应该写：

> 用户首先用较宽的 V1 定义获取事件基线数据，浏览 Live Data 后发现“幕后黑手”“全网下架”“史翠珊效应”等实际叙事，再创建 V2 扩展查询。系统保留 V1 并只允许 V2 active，因此后续 Crawl 可以追溯到具体调查范围版本。

---

# 97. 示例：Finding 的教学解释

> Agent Artifact 不是正式结论。Artifact 中的结构化分析经过 deterministic materialization 后进入 candidate Finding；用户在 Finding 页面看到证据和来源，再提交 Human Review。只有 Review decision 才能把 Finding 变成 verified/rejected。

---

# 98. 示例：Report 的教学解释

> Report Agent 产生 Report Artifact，但 Artifact 仍不是正式发布物。系统将其导入 ReportDocument draft；用户编辑后尝试 Publish。Citation Gate 会验证报告里的 Evidence/Finding/Artifact 引用是否真实且属于同一 Case，通过后才能成为 published Report。

---

# 99. 示例：Monitor 的教学解释

> Investigation 的 8/10–8/20 时间窗口不会随着当前日期移动。完成复盘后，用户创建 Monitor 继续观察事件未来的长尾讨论。这样历史调查和持续监测具有不同的数据语义。

---

# 100. 外部参考源仅用于评审

执行智能体最终可以在结果报告附录写：

```text
Public background references used for reasonableness comparison
```

建议包括：

- 鸿蒙智行 8 月 4 日关于“竹知了”相关网络信息的情况说明；
- 8 月 10 日前后的公开舆情统计/复盘；
- 8 月 11 日媒体舆情复盘；
- 8 月中旬仍出现“竹知了”作为网络符号的公开讨论。

不要把某一家媒体的评论观点定义成 Ground Truth。

---

# 101. 禁止行为

禁止：

```text
直接 seed SocialPost
直接 seed Claim
直接 seed Evidence
直接 seed Propagation
直接 seed Finding
直接 seed Review
直接 seed Report
直接 seed Alert
```

本案例必须以：

```text
真实 crawler
```

为 Source Data 起点。

---

# 102. 禁止为了测试成功修改时间窗口

正式 Investigation 永远：

```text
2026-08-10 ～ 2026-08-20
```

---

# 103. 禁止把外部 Web 搜索替代系统 Crawler

执行智能体可以在最终评审阶段使用 Web 验证合理性。

但不能：

```text
Web Search 找到20篇文章
→ 直接写进数据库
→ 说 Crawler PASS
```

---

# 104. 禁止边测试边改代码

发现问题：

```text
记录 defect
```

不修。

---

# 105. 最终判定

## PASS

```text
真实采集成功
核心 Investigation 链完整
Evidence discipline 正常
Human Review 正常
Report Citation Gate 正常
主要用户 Workspace 正常
```

## PASS WITH FINDINGS

核心链成功，但存在非阻断问题。

## PARTIAL

主要受：

```text
平台认证
LLM
Worker
Crawler 外部环境
```

阻断。

## FAIL

系统自身核心链存在结构性失败。

---

# 106. 建议执行时间

真实 Crawl + Agent 分析可能耗时较长。

执行智能体不要设置：

```text
任意短超时
```

Crawler 当前可能单平台执行较久。

建议完整案例预留：

```text
1–3 小时
```

视平台登录和 LLM 情况。

---

# 107. 不要求跑 Full Regression

本任务不是代码修改。

完成案例后：

```text
不运行全量 pytest
```

只需：

```text
案例本身的运行证据
```

如果测试过程中未修改产品代码，Regression 不适用。

---

# 108. 最终交付物

必须提交：

```text
docs/learning-case-huawei-zhuzhiliao-20260810-0820-result.md
```

建议附：

```text
screenshots/
```

但截图不是强制。

---

# 109. 建议截图

```text
01-home.png
02-investigation-overview.png
03-collection-v1.png
04-crawl-approval.png
05-live-data.png
06-timeline.png
07-agent-trace.png
08-evidence.png
09-network.png
10-findings.png
11-review.png
12-report-draft.png
13-report-published.png
14-signals.png
15-activity.png
```

---

# 110. 最终执行顺序

执行智能体必须按用户真实使用顺序：

```text
Home
→ Create Investigation
→ Goal / Plan
→ Collection V1
→ Crawl + Approval
→ Live Data
→ Collection V2
→ Incremental Crawl
→ Timeline
→ Agent Investigation
→ Evidence
→ Network
→ Findings
→ Human Review
→ Provenance
→ Report Agent
→ Report Draft
→ User Edit
→ Citation Gate
→ Publish
→ Revision
→ Global Reports
→ Activity / Run Trace
→ Monitor
→ Signal
→ Final Result Document
```

---

# 111. 本案例最终要让用户理解的系统逻辑

本次案例跑完后，结果必须能够清楚展示：

```text
用户先定义“要调查什么”
        ↓
Collection Definition

系统采集“现实世界公开信息”
        ↓
Source Posts / Comments / Media

Agent 组织和解释信息
        ↓
Run / Tool / Artifact

系统把分析落成可管理调查对象
        ↓
Claim / Evidence / Propagation / Finding

人类对高风险结论负责
        ↓
Review

系统把经过治理的结论组织成正式输出
        ↓
ReportDocument

确定性规则阻止错误引用被发布
        ↓
Citation Gate

历史 Investigation 完成后
用户继续观察未来变化
        ↓
Monitor / Signal
```

---

# 112. 核心产品心智

本案例不是在验证：

```text
“ChatGPT 能不能回答华为竹知了事件？”
```

而是在验证：

> Nothing-in-the-dark 能否让一个真实用户，把一个复杂、争议性强、来源混杂的公开网络事件，从“设定调查范围”开始，经过真实数据采集、Agent 辅助分析、证据组织、传播复原、人工审核和报告治理，最终形成一份可追溯、可审查并可持续监测的调查结果。

这才是本案例的最终验收目标。
