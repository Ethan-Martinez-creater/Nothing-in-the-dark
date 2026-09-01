---
name: social-crawl
version: 1.1.0
description: Asynchronous progressive social collection (background CollectionRun)
tools: [start_social_collection, get_collection_run]
permissions: [crawl_platform, write_database, read_database]
inputs: [phase, platforms, time_range]
outputs: [collection_run_id, status, platform_progress]
cost_tokens: 400
cancellation: checkpointed
---
# Social Crawl

启动真实社交平台采集时，必须遵循以下固定行为：

1. 使用 `start_social_collection` 启动后台采集（phase 为 discovery 或 deep）。
2. CollectionRun 创建成功即代表采集已经启动；工具立即返回 `collection_run_id`。
3. 当前 Turn 不等待采集结束，不 sleep、不轮询。
4. 不主动循环调用 `get_collection_run` 等待任务完成。
5. 告知用户后台采集任务正在执行。
6. 告知用户第一批数据会渐进出现在 Live Data，完整采集会继续进行。
7. 用户可以继续对话、查看 Live Data、发起阶段性分析或取消剩余采集。
8. 对 partial data 做分析时，必须明确说明"当前覆盖仍不完整"。

只有在用户明确询问采集进度时，才调用 `get_collection_run` 读取状态。
