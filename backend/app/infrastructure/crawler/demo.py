from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.ports.crawler import CrawlRequest
from app.services.crawl_coverage import effective_caps, iter_days, resolve_window

# 每平台演示模板：按事件演化阶段（起因 → 发酵 → 高峰 → 回应）组织，
# 让 demo 模式下的分析（观点/传播/核查）有阶段结构可挖。
_TEMPLATES: dict[str, list[tuple[str, str, str]]] = {
    "weibo": [
        ("现场观察员", "最早的现场信息提到了{topic}，目前仍需要等待官方说明。", "neutral"),
        ("本地资讯站", "关于{topic}的讨论快速增加，多名用户引用了同一段现场描述。", "negative"),
        ("热搜围观者", "{topic}已经冲上同城热搜，转发里都在要完整时间线。", "neutral"),
        ("机构发布", "针对{topic}，已发布阶段性情况说明，请以正式通报为准。", "positive"),
        ("评论员老周", "{topic}发酵的关键不是事件本身，而是前后说法不一致。", "negative"),
        ("辟谣小队", "网传{topic}的聊天记录截图经过拼接，原始出处另有其人。", "positive"),
        ("法律博主", "从责任认定角度看{topic}，目前公开证据还不足以定性。", "neutral"),
        ("普通用户甲", "看了{topic}的几个视频，前后信息差太大，先让子弹飞一会儿。", "neutral"),
    ],
    "bilibili": [
        ("记录与核查", "视频梳理：{topic}事件时间线与多个版本的说法。", "neutral"),
        ("社会议题研究所", "从评论区看，{topic}的关注点已从事件本身转向信息透明度。", "negative"),
        ("事实显微镜", "核查发现一张广泛传播的截图早于本次{topic}事件。", "positive"),
        ("弹幕考古组", "{topic}的视频密度在 48 小时内翻了三倍，二创切片开始扩散。", "neutral"),
        ("深度长文区", "用传播学视角拆解{topic}：情绪浓度高的片段总是先出圈。", "negative"),
        ("技术流分析", "对{topic}相关视频做了转发曲线拟合，峰值出现在通报发布前。", "neutral"),
    ],
    "zhihu": [
        ("理性讨论者", "关于{topic}，把目前可靠的信息源梳理了一下，去掉情绪化表述。", "neutral"),
        ("行业从业者", "从业内角度看{topic}，这类流程上的漏洞其实早有先例。", "negative"),
        ("考据爱好者", "查了{topic}里被引用最多的那段话，原始语境被截掉了后半句。", "positive"),
        ("法律话题答主", "{topic}涉及的责任边界，需要区分平台方与当事方的义务。", "neutral"),
        ("信息溯源实验室", "对{topic}的三种流行说法做了交叉验证，只有一种能对上时间。", "positive"),
        ("围观群众乙", "{topic}的高赞回答基本都在复读同一个论点，独立信源很少。", "negative"),
    ],
    "douyin": [
        ("现场直击号", "15 秒带你看懂{topic}的来龙去脉。", "neutral"),
        ("反转记录仪", "{topic}又双叒反转了？这次是当事方的最新回应。", "negative"),
        ("热点追踪君", "{topic}话题播放量破千万，评论区已吵翻。", "neutral"),
        ("辟谣快报", "注意：关于{topic}的这条语音消息是 AI 合成的。", "positive"),
        ("情绪观察室", "刷{topic}相关视频，愤怒和玩梗的比例大概七三开。", "negative"),
        ("慢新闻", "别人都在抢{topic}的热点，我们把时间线完整走了一遍。", "neutral"),
    ],
    "tieba": [
        ("楼主阿强", "有懂的吗？{topic}到底是怎么回事，层主们补充一下。", "neutral"),
        ("资深吧友", "按惯例，{topic}这种事大概率过两天就有官方结论。", "neutral"),
        ("暴躁老哥", "{topic}处理成这样，换谁不骂？", "negative"),
        ("理性分析帝", "别急着站队，{topic}的完整监控还原还没放出来。", "positive"),
        ("吃瓜前线", "{topic}的后续来了，和最开始传的版本差别不小。", "neutral"),
        ("贴吧考古员", "翻到{topic}半年前的旧帖，原来早有苗头。", "positive"),
    ],
}


class DemoCrawlerAdapter:
    """Deterministic adapter used until the MediaCrawler submodule is connected.

    覆盖全部支持平台，发布时间均匀散布在所选时间范围内（缺省为近 72
    小时），条数受 ``limit_per_platform`` 约束，保证 demo 模式下的多平台
    对比与辩论也有可用数据。
    """

    async def collect(self, request: CrawlRequest) -> list[dict[str, object]]:
        start, end = resolve_window(request.time_range)
        days = iter_days(start, end)
        per_day, comment_limit = effective_caps(request)

        posts: list[dict[str, object]] = []
        for platform in request.platforms:
            templates = _TEMPLATES.get(platform)
            if not templates:
                continue
            for day_index, day in enumerate(days):
                daily = templates[:per_day]
                for index, (author, content, sentiment) in enumerate(daily):
                    published = datetime(
                        day.year, day.month, day.day, 8 + (index % 12), tzinfo=UTC
                    )
                    if published < start:
                        published = min(start + timedelta(minutes=index + 1), end)
                    if published > end:
                        published = max(end - timedelta(minutes=index + 1), start)
                    comments = [
                        {
                            "native_id": f"demo-{platform}-{day_index}-{index}-c{cidx}",
                            "content": (
                                "文明发言。"
                                if cidx == 0
                                else f"补充看法 {cidx}：{request.topic}"
                            ),
                            "engagement": 20 - cidx,
                            "metrics": {"like_count": 20 - cidx},
                        }
                        for cidx in range(comment_limit + 2)
                    ]
                    posts.append(
                        {
                            "id": f"demo-{platform}-{day_index + 1}-{index + 1}",
                            "platform": platform,
                            "author": author,
                            "content": content.format(topic=request.topic),
                            "published_at": published.isoformat(),
                            "sentiment": sentiment,
                            "engagement": 90 + index * 137 + day_index * 11,
                            "url": (
                                f"https://example.invalid/{platform}/"
                                f"{day_index + 1}/{index + 1}"
                            ),
                            "is_demo": True,
                            "comments": comments,
                        }
                    )
        return posts
