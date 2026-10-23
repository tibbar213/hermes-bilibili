# 项目结构与适配接口

```text
plugin.yaml / __init__.py   原生注册工具、CLI、记忆刷新 hook
host.py                    profile 绑定的后台循环与控制入口
ipc.py                     私有 Unix socket 与单实例文件锁
context.py                 Hermes 模型、人格、主人通知接口
shared_memory.py           Hermes 原生长期记忆和活动记录维护
settings.py                默认值、策略上限、凭据分离
runtime.py                 上游业务流程的原生运行入口
core/                      B 站 API、字幕、事件队列、调度与数据层
assets/fonts/              可选报告渲染字体，独立 OFL 许可证
tests/                     原生适配行为测试
examples/                  不含真实身份与凭据的配置示例
```

gateway 持有运行实例和 profile/bilibili/runtime.lock；其他进程经 runtime.sock 请求复用。注册工具或运行普通 CLI 不会自动开启第二个轮询器。后台任务使用 B 站自身会话上下文，不继承 QQ 主人的任务授权。

## Hermes 接口边界

依赖原生插件 register(ctx)、工具 registry、CLI 注册和 pre_llm_call hook；模型使用 agent.auxiliary_client；profile 凭据使用 agent.secret_scope；长期记忆使用 tools.memory_tool 的内置 MemoryStore；身份来自 gateway.session_context。

这些接口会随 Hermes 演进，更新时检查实际源码和测试，不维护固定版本的核心文件覆盖层。当前目录不应注入假 astrbot 包，也不应修改 Hermes 核心来绕过权限。

core 保留部分上游旧术语和可选路径，迁移来源由 UPSTREAM.json 追踪。原生入口的行为以 README、配置文档和测试为准。上游 WebUI、AstrBot 命令装饰器和完整 AstrBot 扩展生态未移植。

## 测试

在具有 Hermes 源码/依赖的环境中使用该版本的官方测试运行器。例如将本项目的 tests/test_native.py 作为测试目标传给 Hermes scripts/run_tests.sh。测试以临时目录隔离凭据、记忆、预算和 socket，不访问真实 B 站账号。

远端历史实测记录见 docs/VERIFICATION.md。测试不证明所有 Hermes 版本兼容，也不代表公开评论、动态或弹幕已经真实外发。
