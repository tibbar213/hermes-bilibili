# 活动流水与额度

`bili_recall(memory_type="today")` 汇总当天记录；`memory_type="activity"` 查询浏览和主动评论流水，`query` 可填 YYYY-MM-DD，`activity_kind` 可选 all / browse / comment，`limit` 为 1–100。查询结果附带该日期的预算计数。`bilibili_manage(action="status")` 也会返回今日已用额度。

流水保存在当前 profile 的 `bilibili/bilibot.sqlite3` 的 `native_activity` 表，与长期记忆分开。浏览记录区分字幕阅读、外部资料、旧缓存、资料不可用和额度拦截；主动评论记录正文、视频、时间、已确认/失败/未知状态，接口提供评论 ID 时一并保存。重复回执不会重复记账，也不会用后来未发送的文字覆盖已发送的正文。写本地记录失败不触发外部消息重发。

流水条数不等于看片额度用量：未成功并已退额的尝试仍可留在流水，但不占预算；结果不明确的发送按现有规则保留预算。额度使用原有原子计数器，不根据记录推算，不修改上限。评论回复和动态继续沿用原有日志。

`AUTONOMOUS_REPLY_DAILY_LIMIT=0` 表示评论回复每日不限，次数仍计入回复计数，但后续回复不消耗总日预算，避免被总日上限间接限制；每分钟限速仍生效。状态查询中 `limit=null` 表示没有该项日上限。其他类别仍要求正整数上限，动态上限通过 `AUTONOMOUS_DYNAMIC_DAILY_LIMIT` 配置。

升级后开始自动记录。历史回填只允许从确切工具回执恢复；不能把简介推测补成已观看，也不能凭后来重复提交的文字推断原评论正文。没有回执的历史不承诺完整。查询不会发布评论、动态或群消息。

## 评论回复发送保护

可在 profile 的 bilibili/settings.json 配置 COMMENT_REPLY_MIN_INTERVAL（秒，未配置为 0）、COMMENT_REPLY_HOURLY_LIMIT（滚动一小时，未配置为 0），以及 AUTONOMOUS_REPLY_DAILY_LIMIT（日额度）。例如分别设置 120、12、30。发送前使用 SQLite 原子占位，失败和结果未确认也占发送节流名额，避免自动重试形成连发；现有业务预算退款规则不变。状态中的 request_guard_now 返回当前间隔、次数和恢复时间，查询历史日期时它仍代表当前发送保护状态。

插件统一 HTTP session 共用单并发、读取/写入预算、发送间隔和递增风控暂停；重启保留状态，模型服务不受影响。完整参数、字幕证据、私信补读与评论回读见 [请求保护与参考适配](REFERENCE_ADAPTATION.md)。
