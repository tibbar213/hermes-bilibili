# Hermes Bilibili

Hermes 原生 B 站插件：让同一个代理在 B 站搜索、读字幕、回复评论与私信、自主浏览、发动态，并参与直播弹幕互动。

复用 Hermes 的模型、人格和长期记忆；不需要 AstrBot 服务或独立 MCP 服务，也不管理另一套 API 渠道。

## 安装

推荐把 [安装提示词](INSTALL_AGENT.md) 交给能操作服务器的 Agent，按当前 Hermes 接口完成安装与核验。手动安装见 [安装文档](docs/INSTALL.md)。当前支持 Linux。

仓库根目录就是原生插件目录，安装到当前 Hermes profile 的 `plugins/hermes-bilibili`，保留 plugin.yaml 与 __init__.py 同级。配置合并示例位于 [examples](examples/)，示例默认关闭自动外发。

## 当前行为

- 视频只读取经过归属校验的字幕；没有字幕、获取失败或字幕错配就跳过，不下载视频做视觉分析。
- 长期事实读写 Hermes 内置 MemoryStore；B 站只单独保留会话上下文、媒体缓存、去重与额度记录。
- 直播读取弹幕，不读取主播音频或画面；默认房间号为 0，不进入直播间。
- 自动行为上限：浏览 3/天、动态 1/天、评论回复 20/天、私信回复 20/天；自动投币和关注关闭。
- 调度由 gateway 单实例持有，CLI/子代理经私有 Unix socket 复用。
- QQ 账号管理仅限配置的主人，工具写操作需结合现有任务授权机制。插件本身不包含 QQ 渠道适配器。

## 使用

```sh
hermes bilibili status
hermes bilibili check
hermes bilibili login
hermes bilibili confirm
hermes bilibili plan
hermes bilibili live --room-id 0
```

扫码会话和后台运行依赖 gateway。dynamic 子命令会真实发布动态，live 的非零房间号会启动弹幕监听。完整配置、工具列表和权限边界见 [配置文档](docs/CONFIGURATION.md)。

## 开发与分发

项目结构和 Hermes 接口边界见 [架构文档](docs/ARCHITECTURE.md)。原生行为测试需要 Hermes 环境；历史实测范围见 [验证记录](docs/VERIFICATION.md)。当前通过 18 项适配测试，不承诺所有 Hermes 版本兼容。

本项目按 plugin.yaml 安装，不是 pip 包。pyproject.toml 只配置开发工具。

```sh
python scripts/package.py --check
python scripts/package.py
```

源码包输出到 dist/，附带 SHA-256 文件与包内源码哈希清单。打包器只纳入项目源码、文档、示例和许可证，不收集运行目录。发布前仍应检查自行添加的内容。

## 来源

业务核心移植自 [astrbot_plugin_bilibili_ai_bot](https://github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot)，具体提交见 [UPSTREAM.json](UPSTREAM.json)。代码使用 [MIT](LICENSE)，内置 Noto 字体使用 [SIL OFL 1.1](assets/fonts/OFL.txt)。详见 [NOTICE](NOTICE.md) 和 [更新记录](CHANGELOG.md)。
