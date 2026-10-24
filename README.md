# Hermes Bilibili

Hermes 原生 B 站插件：让同一个代理在 B 站搜索、读字幕、回复评论与私信、自主浏览、发动态，并参与直播弹幕互动。

复用 Hermes 的模型、人格和长期记忆；不需要 AstrBot 服务或独立 MCP 服务，也不管理另一套 API 渠道。

## 安装

推荐把 [安装提示词](INSTALL_AGENT.md) 交给能操作服务器的 Agent，按当前 Hermes 接口完成安装与核验。手动安装见 [安装文档](docs/INSTALL.md)。当前支持 Linux。

仓库根目录就是原生插件目录，安装到当前 Hermes profile 的 `plugins/hermes-bilibili`，保留 plugin.yaml 与 __init__.py 同级。配置合并示例位于 [examples](examples/)，示例默认关闭自动外发。

## 当前行为

- 选片结合登录账号首页推荐、关注 UP 今天的视频更新和主动搜索；先看标题与简介，跳过已看过和重复候选。
- 视频只读取经过有效性校验的字幕，不下载视频、不抽帧。无可靠字幕时可根据标题、简介、弹幕和评论推测，明确标注来源，不记成已观看。
- 长期事实读写 Hermes 内置 MemoryStore；B 站只单独保留会话上下文、媒体缓存、去重与额度记录。
- 直播读取弹幕，不读取主播音频或画面；默认房间号为 0，不进入直播间。
- 自动行为上限：浏览 24/天、每轮最多 3 个、主动评论 6/天、动态 1/天、评论回复 20/天、私信回复 20/天；自动投币和关注关闭。
- 自主生活和缓存维护使用 Hermes 原生 cron；B 站后台只负责消息监听和连接维护。CLI/子代理经私有 Unix socket 复用连接。
- B 站回复进入真实 Hermes AIAgent 会话与工具循环；辅助模型仅用于摘要、筛选等内部步骤。
- 外部消息和自主生活没有终端、文件、安装、权限管理等服务器工具；QQ 主人授权机制独立保留。
- QQ 账号管理仅限配置的主人，工具写操作需结合现有任务授权机制。插件本身不包含 QQ 渠道适配器。

## 使用

```sh
hermes bilibili status
hermes bilibili check
hermes bilibili login
hermes bilibili confirm
hermes bilibili plan
hermes bilibili life_start
hermes bilibili life_pause
hermes cron list
hermes bilibili live --room-id 0
```

扫码会话和后台运行依赖 gateway。dynamic 子命令会真实发布动态，live 的非零房间号会启动弹幕监听。完整配置、工具列表和权限边界见 [配置文档](docs/CONFIGURATION.md)。

## 开发与分发

项目结构和 Hermes 接口边界见 [架构文档](docs/ARCHITECTURE.md)。原生行为测试需要 Hermes 环境；历史实测范围见 [验证记录](docs/VERIFICATION.md)。各轮测试范围见验证记录，不承诺所有 Hermes 版本兼容。

本项目按 plugin.yaml 安装，不是 pip 包。pyproject.toml 只配置开发工具。

```sh
python scripts/package.py --check
python scripts/package.py
```

源码包输出到 dist/，附带 SHA-256 文件与包内源码哈希清单。打包器只纳入项目源码、文档、示例和许可证，不收集运行目录。发布前仍应检查自行添加的内容。

## 来源

业务核心移植自 [astrbot_plugin_bilibili_ai_bot](https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot)，具体提交见 [UPSTREAM.json](UPSTREAM.json)。代码使用 [MIT](LICENSE)，内置 Noto 字体使用 [SIL OFL 1.1](assets/fonts/OFL.txt)。详见 [NOTICE](NOTICE.md) 和 [更新记录](CHANGELOG.md)。


## 原生自主生活

`NATIVE_LIFE_ENABLED` 控制原生生活任务安装，默认未开启；由主人执行 `life_start` 开启、`life_pause` 暂停。首次创建 fish-life（08:00—22:00 每两小时一轮）与 fish-home-maintenance（03:30），使用 Hermes 配置时区。以后可以在原生 cron 中调整或暂停，重启不会覆盖这些修改。

生活任务可以阅读公开互联网、读取视频字幕、记录新事实，以及在预算内自主评论视频、发动态和向授权群分享视频。它可以选择什么都不做，结果保留本地任务记录；故障投递给主人私聊。QQ群只从显式授权名单与已有渠道交集中选择，且最近10分钟有活动才考虑说话；每群每天最多4次、至少间隔1小时，同一视频当天不重复分享。实际投递仍受 QQ 官方主动消息权限和窗口限制。房间号0仍不加入直播。
