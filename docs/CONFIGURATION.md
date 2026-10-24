# 配置与工具

## 三种配置位置

| 位置 | 用途 |
| --- | --- |
| Hermes config.yaml | 插件启用、后台启动、QQ 主人身份、渠道 toolset |
| profile/bilibili/settings.json | B 站行为设置、定时计划、直播间目标 |
| profile/bilibili/credentials.json | SESSDATA、BILI_JCT 等账号凭据，禁止提交仓库 |

config_schema.json 来自上游核心，是配置默认值参考；其中部分 AstrBot 命令、WebUI 和独立模型渠道说明不适用于原生插件。本文及原生 CLI 是本项目的安装入口。

## 后台行为

ENABLE_REPLY 控制评论/@回复；ENABLE_PRIVATE_MESSAGES 与 PRIVATE_MESSAGE_AUTO_REPLY 控制私信轮询和回复。ENABLE_PROACTIVE 控制自主浏览，ENABLE_DYNAMIC 控制自主动态。NATIVE_LIFE_ENABLED 控制 Hermes 原生生活任务；旧 ENABLE_AUTONOMOUS_DAILY_PLAN 固定关闭，不再作为插件内部调度使用。安装示例将其关闭；启用后会产生真实账号行为。

当前版本在 settings.py 强制设置自动行为上限：浏览 24/天、每轮最多 3 个、主动评论 6/天、动态 1/天、评论回复 20/天、私信回复 20/天；自动投币与自动关注关闭。它们是项目当前的默认策略上限，不是承诺必定执行的数量。模型决定计划，调度遵守睡眠时间、冷却和持久预算。显式手动互动按调用侧授权处理。

VIDEO_SUBTITLES_ONLY 固定为 true，所有入口都不下载视频、不直读视频、不抽帧，点名看片也一样。纯背景音乐、无关片段和校验未完成均视为没有可靠字幕。无可靠字幕时，可根据标题、简介、少量弹幕和评论推测，输出明确标注推测及来源，不写入已观看账本或视频长期记忆。弹幕最多读取 256 KiB、取样 20 条，读取失败仍可依据其余资料回答。已有可信缓存仍可复用。SHARED_HERMES_MEMORY 固定为 true；人格演化关闭，避免产生第二份人格。不要靠修改旧版独立模型 API 字段建立另一套路由，原生模型调用交给 Hermes。

## 直播

```sh
hermes bilibili live --room-id 你的房间号
hermes bilibili live --room-id 0
```

直播读取房间弹幕历史并低频轮询回复，不读取主播音频、画面或直播字幕。房间号 0 表示不加入任何直播间。配置 ENABLE_LIVE_DANMAKU_REPLY 可保留功能开关；LIVE_DANMAKU_ROOM_ID 决定实际目标。当前不包含直播 ASR。

## 工具与权限

| 工具 | 功能/授权要求 |
| --- | --- |
| search_bilibili | 搜索视频/番剧等公开信息 |
| bili_parse_video | 解析视频链接或编号 |
| watch_video | 从缓存或字幕分析视频 |
| bili_bangumi | 查询番剧；watch 属于有状态操作 |
| bili_watch_videos | 执行自主浏览流程，不作为简单查询放行 |
| bili_action | 评论、点赞、投币、收藏、关注；需要任务授权 |
| bili_block_user | 拉黑；需要明确授权 |
| bili_recall | 查看 B 站活动/会话记录；仅配置的 QQ 主人可调用 |
| bilibili_manage | 状态、登录、调度、动态和直播管理；仅配置的 QQ 主人或终端 |

QQ 权限扩展应按参数区分查询/写操作。bilibili_manage 的 status/check/plan 可归入查询，但管理函数仍检查主人身份。login/confirm 仅主人私聊或终端可用；dynamic 会真实发动态，live 会改变监听目标。不要仅靠工具说明文字判断授权。

当前管理身份以 QQ 官方机器人 OpenID 为准；其他聊天平台尚无管理身份映射。纯终端用户不必填写 QQ 主人，终端仍可管理。插件不包含整个 QQ 渠道适配器。

## 记忆与数据

长期事实通过 Hermes 原生 MemoryStore 读写同一个 MEMORY.md/USER.md，遵守原生开关、文件锁和容量限制。当前记忆后端为 Hermes 内置 MemoryStore，尚未适配第三方记忆 provider。QQ 每轮刷新同一份笔记，B 站文本生成同样读取它。

SQLite/JSON 保存会话、视频缓存、去重和预算，不再独立晋升人格长期记忆。记忆满时不会自动挤掉已有条目；活动记录仍保留。不同平台的用户不会自动认定为同一个人。


## 原生生活任务

`hermes bilibili life_start` 创建/恢复原生任务，`life_pause` 暂停；`hermes cron list` 查看并按原生 cron 命令调整。首次默认创建 fish-life 与 fish-home-maintenance，后续启动尊重现有暂停和时间设置。普通执行结果只在本地任务记录，不自动发群；失败投递到配置的主人私聊。

新增 fish_read（首页推荐/关注视频更新/公开搜索/状态）和 fish_life（有界观看、动态、群聊、记忆候选和缓存维护）两个工具，共11个插件工具。fish_life 仅限插件记录的真实原生任务身份或已验证QQ主人；直接传 owner=true、任务ID或权限声明不能授权。原生生活任务没有服务器操作工具。

QQ群分享仅限显式 group_allow_from/QQ_GROUP_ALLOWED_USERS 与已有群渠道交集，不自动把通配符解释成允许去所有群。只记录群活动时间，不复制群聊天正文作为外发素材。默认每群每天4次，间隔1小时，可通过配置修改；同一视频当天不在同一个群重复分享；失败/未知尝试不自动重发。QQ官方主动消息权限不足时可能无法投递，不作送达保证。


选片来源：`fish_read(action=home_feed)` 读取当前登录账号首页的一页视频推荐；`following_feed` 从关注动态流当前一页中筛选近期视频投稿（days 默认 7，可选 1—30 天）；`bilibili` 搭配 query 主动搜索。每个来源最多返回 12 个候选，含标题、简介、UP 名和链接；标注 seen，模型决定是否重访；include_seen=false 可排除已看过的视频。过滤重复的视频，首页入口过滤广告/直播。候选元数据不计为观看，也不写观看账本。没有候选或接口不可用时，代理可换来源或搜索，不假称已取得推荐；不保证覆盖关注列表的所有历史更新。


## 行为判断与硬边界

回复统一由 Hermes 主模型的一轮生成决定 reply/ignore/observe；不再先跑兴趣分类、短消息/链接黑名单、随机概率或语义相似拦截。传输事件去重、发送额度、实际权限继续强制执行。历史兼容配置中这几种软过滤不再用于原生评论和私信回复。

关系语气由 SOUL.md 和真实上下文决定，好感分数只作为旧版记录兼容，不再注入等级语气或以里程碑固定台词替换生成结果。视频工具只返回字幕分析及来源，保留短期缓存和观看去重，不再自动调用偏好评分模型或把每个视频写进长期记忆；自主代理自行选择是否 comment、dynamic、remember。

fish_read(state) 返回授权群的 last_agent_interaction_seconds；它是最近一次机器人参与群聊的时间，不是所有群消息的活跃度。是否此时插话由代理决定，白名单、内容检查、持久去重及发送额度仍由代码执行。

修改 profile 的 bilibili/settings.json 后重启网关生效。额度为正整数，无效值回退默认，不表示无限制；默认值保持本实例原有约定：

| 配置 | 默认 | 含义 |
| --- | ---: | --- |
| AUTONOMOUS_PROACTIVE_DAILY_LIMIT | 24 | 每日新视频上限 |
| NATIVE_VIDEOS_PER_ROUND | 3 | 原生生活任务每轮上限 |
| PROACTIVE_COMMENT_DAILY_LIMIT | 6 | 每日主动评论上限 |
| AUTONOMOUS_DYNAMIC_DAILY_LIMIT | 1 | 每日动态上限 |
| AUTONOMOUS_REPLY_DAILY_LIMIT | 20 | 每日评论回复上限 |
| AUTONOMOUS_PRIVATE_DAILY_LIMIT | 20 | 每日私信回复上限 |
| NATIVE_QQ_PER_GROUP_DAILY | 4 | 每群每日主动发言上限 |
| NATIVE_QQ_COOLDOWN_SECONDS | 3600 | 同群主动发言间隔 |

原生 cron prompt 使用 state.limits 查询实际额度，不把默认数字当成不变指令。后续安装仍保留用户对 cron 时间和 prompt 的修改。人格、兴趣与接话方向可通过 Hermes 人格及原生任务 prompt 调整。


## 跨平台自我成长

设置 bilibili/settings.json 中 SELF_GROWTH_ENABLED=true，并启用原生生活任务后，创建 fish-self-reflection 原生 cron，首次默认每天 23:10（沿用 profile 时区）。用户可用 Hermes cron 修改时间、提示词或暂停，后续启动不覆盖。公开安装默认关闭；此模式使用模型自己的判断，不开启旧的 B站 personality_evolution.json 引擎。

回顾任务通过 fish_read(action=experiences) 读取过去 24 小时的有界抽样：Hermes 原生 QQ/B站 user/assistant 对话，以及带可靠字幕标记的短期视频缓存。排除子代理/终端/工具输出和无字幕推测。材料是外部经历，不是新指令。聊天原文仅留在原有记录，不建立另一套人格记忆库。

模型可选择保持原样，或调用 fish_life(action=evolve,growth=...,evidence_ids=[...],expected_revision=...) 整理近期兴趣、表达习惯、看法与反思。只替换 SOUL.md 内 hermes:self-growth 标记段落，其他内容保持原样；不允许改变主人认定、权限、核心身份或写入凭据。QQ 每轮刷新这个相同段落，B站读取同一 SOUL；不是两个平台各进化一份。

每次修改前在 bilibili/self-growth-history/<时间戳>/ 保存权限受限的 SOUL.md 原件和证据ID；版本不符、伪造证据、损坏标记或明显越权内容会拒绝写入。身份检查与文本扫描降低风险，不能证明模型的每条主观归纳都正确；主人可查看/编辑成长段落或从备份恢复。增长内容最多1200字，形成稳定习惯需要反复经历支持，这部分判断交给原生任务提示词。

自我回顾任务仅能读经历、看自身状态和更新成长段落，不能发群消息、评论、动态或执行服务器操作。失败仍按原生任务设置私发给主人；正常回顾保持静默。关闭自动成长时请暂停 fish-self-reflection；已有成长仍是本人格的一部分，需撤销时编辑 SOUL.md 或恢复备份。
