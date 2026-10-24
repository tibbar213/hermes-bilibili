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

QQ群分享仅限显式 group_allow_from/QQ_GROUP_ALLOWED_USERS 与已有群渠道交集，不自动把通配符解释成允许去所有群。只记录群活动时间，不复制群聊天正文作为外发素材。每群每天4次，间隔1小时；同一视频当天不在同一个群重复分享；失败/未知尝试不自动重发。QQ官方主动消息权限不足时可能无法投递，不作送达保证。


选片来源：`fish_read(action=home_feed)` 读取当前登录账号首页的一页视频推荐；`following_feed` 从关注动态流中筛选今天的视频投稿；`bilibili` 搭配 query 主动搜索。每个来源最多返回 12 个候选，含标题、简介、UP 名和链接；过滤已看过和重复的视频，首页入口过滤广告/直播。候选元数据不计为观看，也不写观看账本。没有候选或接口不可用时，代理可换来源或搜索，不假称已取得推荐；不保证覆盖关注列表的所有历史更新。
