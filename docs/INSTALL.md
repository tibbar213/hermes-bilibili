# 安装

这是目录式 Hermes 原生插件。仓库根目录就是插件目录，plugin.yaml 与 __init__.py 必须保持同级。当前支持 Linux；本地 IPC 使用 Unix socket 与 fcntl，尚未支持 Windows gateway。

## 通过 Agent 安装

推荐把 [INSTALL_AGENT.md](../INSTALL_AGENT.md) 交给服务器上的 Agent。安装时以当前 Hermes 的真实接口为准；本项目未声明跨版本兼容范围，也不要求固定或回退到某个 Hermes 提交。

## 手动安装步骤

1. 确认当前 profile 的 Hermes 配置目录，通常为 ~/.hermes。备份已有同名插件、config.yaml、bilibili 数据目录和 memories；SQLite 应使用一致性备份。
2. 将仓库复制到 plugins/hermes-bilibili。依赖见 requirements.txt；由 Hermes 当前环境管理器补齐缺少的库。默认字幕模式不需要 ffmpeg/yt-dlp；只有显式视频文件分享等可选媒体路径需要这些外部程序。
3. 将 examples/hermes-config.example.yaml 中的字段合并进现有 config.yaml，不能整份覆盖。plugins.enabled 是插件加载开关，entries 中的 settings.enabled 是后台启动开关。
4. 根据实际需要创建 bilibili/settings.json，参考 examples/bilibili-settings.example.json。初次安装的示例关闭自动外发，账号确认后再启用所需功能。该文件与 credentials.json 的目录权限应分别为 700/600。
5. 正常启动或重启 Hermes gateway。运行 hermes bilibili login，打开输出链接并用 B 站扫码，再运行 hermes bilibili confirm。成功后用 check 检查。gateway 未运行时，独立 CLI 不保留扫码会话和调度。
6. 使用 status 检查运行状态，再设置后台启动。需要工具的渠道应包含 bilibili toolset。按 docs/CONFIGURATION.md 接入现有权限策略。

## 核验

```sh
hermes bilibili status
hermes bilibili check
hermes bilibili plan
```

预期原生注册 9 个工具，账号 check 成功，video_subtitles_only 为 true，memory_backend 为 hermes。未指定直播间时 live_room_id 为 0、live_running 为 false。多次执行 CLI 不应新增轮询实例。

## 更新和回滚

更新前备份插件源码、配置和 bilibili 数据目录；运行测试，等待活跃任务结束后替换源码并重启 gateway。回滚恢复对应插件与配置；需要恢复 SQLite 时先停止该插件的写入，防止数据文件不一致。不要把长期记忆整份回退覆盖掉更新后其他会话新增的笔记。
