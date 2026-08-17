<div align="center">
  <h1>🐈 nanobot-self-improve</h1>
  <p><strong>具有记忆自进化能力的轻量级 AI Agent 框架</strong></p>
  <p>
    <a href="#-核心特性">核心特性</a> •
    <a href="#-快速开始">快速开始</a> •
    <a href="#-架构设计">架构设计</a> •
    <a href="#-文档">文档</a>
  </p>
  <p>
    <a href="https://github.com/chidao-cpu/nanobot-self-improve"><img src="https://img.shields.io/github/stars/chidao-cpu/nanobot-self-improve?style=flat&logo=github" alt="GitHub stars"></a>
    <a href="https://pypi.org/project/nanobot-ai/"><img src="https://img.shields.io/pypi/v/nanobot-ai" alt="PyPI version"></a>
    <a href="https://pypi.org/project/nanobot-ai/"><img src="https://img.shields.io/badge/python-%3E%3D3.11-blue" alt="Python 3.11 or newer"></a>
    <a href="./LICENSE"><img src="https://img.shields.io/github/license/chidao-cpu/nanobot-self-improve" alt="MIT License"></a>
  </p>
</div>

---

## 🎯 项目定位

**nanobot-self-improve** 是基于 [nanobot](https://github.com/HKUDS/nanobot) 的增强版本，专注于两个核心创新：

1. **两阶段记忆整合机制** — 实时压缩 + 反思性巩固，让 Agent 真正"记住"并"理解"对话
2. **技能自进化系统** — 从对话中学习、创建、追踪、归档技能，形成闭环的自我提升能力

这两个特性使 nanobot 从一个"无状态的工具调用器"进化为一个**具有持续学习能力的智能体**。

---

## ✨ 核心特性

### 🧠 两阶段记忆整合（Dream Memory System）

传统的 AI Agent 要么完全无状态，要么简单地把所有历史塞进上下文。nanobot 实现了一个**两阶段记忆整合系统**，让 Agent 能够像人类一样"消化"和"整理"对话经验。

#### 第一阶段：实时压缩（Consolidator）

当对话接近上下文窗口限制时，Consolidator 会自动：
- 监控 token 使用量，识别安全的压缩边界
- 将较早的对话片段总结为精简摘要
- 保留关键信息，丢弃冗余细节
- **不再依赖外部历史文件**，直接在会话内完成压缩

#### 第二阶段：反思性巩固（Dream）

Dream 是更深层次的"反思"过程，可以手动触发（`/dream`）或定时执行（默认每 2 小时）：

```
会话消息 → MECE 分类 → 手术式编辑 → Git 版本控制
```

**MECE 分类系统**将信息路由到四个专用文件：

| 文件 | 内容类型 | 示例 |
|------|---------|------|
| **SOUL.md** | Agent 行为规则、交互策略 | "用户偏好简洁的回答风格" |
| **USER.md** | 用户个人属性、习惯 | "用户是 Python 开发者，使用 asyncio" |
| **MEMORY.md** | 项目上下文、技术决策 | "项目使用 Python 3.11+，采用事件驱动架构" |
| **SKILL.md** | 可复用的工作流程模板 | "部署流程：先测试，再构建，最后发布" |

**关键创新**：
- **会话直读**：直接从 session messages 读取，不再依赖 `history.jsonl`，消除同步问题
- **冻结快照**：系统提示使用不可变快照，保持前缀缓存稳定
- **解释性记忆**：Dream 不只是存储事实，而是**分类、去重、路由**信息到规范位置
- **Git 审计**：每次 Dream 都会提交 Git，支持 `/dream-log` 查看变更，`/dream-restore` 回滚

#### §-分隔的原子记忆格式

MEMORY.md 使用 `\n§\n` 分隔符组织原子事实：

```markdown
用户偏好中文交流
§
项目使用 Python 3.11+ 和 asyncio
§
工作区位于 /home/user/project
```

**特性**：
- 4000 字符预算限制
- 语义重叠检测（拒绝重复）
- 批量原子操作（全成功或全失败）

---

### 🔄 技能自进化系统（Skill Self-Evolution）

nanobot 实现了一个**完整的技能生命周期系统**，让 Agent 能够从对话中学习、创建、追踪、归档技能，形成闭环的自我提升能力。

#### 学习入口：`/learn` 命令

用户描述要学习的内容，Agent 自动：
1. 使用工具（read_file、search、web_fetch）收集权威资料
2. 提炼出持久、可复用的流程
3. 调用 `skill_manage` 工具创建 SKILL.md

```bash
/learn 如何优化 Python 异步代码的性能
```

#### 技能管理工具

`skill_manage` 工具支持三个操作：
- **create**：创建新技能到 `workspace/skills/<name>/SKILL.md`
- **patch**：更新现有技能
- **delete**：归档技能（设置状态为 "archived"，永不物理删除）

**安全特性**：
- 路径遍历保护：验证技能目录在 `workspace/skills/` 内
- 名称验证：仅允许字母、数字、连字符、下划线
- 背景审查守卫：限制后台审查期间的自主写入

#### 使用追踪系统

每个技能的使用情况记录在 `workspace/skills/.usage/<name>.json`：

```python
@dataclass
class SkillUsageRecord:
    name: str
    use_count: int = 0              # 使用次数
    view_count: int = 0             # 查看次数
    last_activity_at: float = 0.0   # 最后活动时间
    created_at: float = 0.0         # 创建时间
    created_by: str = "user"        # "user" | "agent" | "builtin"
    state: str = "active"           # "active" | "stale" | "archived"
    pinned: bool = False            # 是否固定
```

**状态机**：
- **ACTIVE → STALE**：超过 `DEFAULT_STALE_AFTER_DAYS`（可配置）未使用
- **STALE → ARCHIVED**：超过 `DEFAULT_ARCHIVE_AFTER_DAYS` 未使用
- **STALE → ACTIVE**：再次使用时自动复活

#### Curator：自动生命周期管理

Curator 是一个**空闲触发**的后台进程（无需 cron 守护进程）：

**触发条件**（必须同时满足）：
1. 距离上次运行至少 7 天（`DEFAULT_INTERVAL_HOURS = 24 * 7`）
2. Agent 空闲至少 30 分钟（`DEFAULT_MIN_IDLE_HOURS = 0.5`）

**安全原则**：
- 仅处理 `created_by == "agent"` 的技能
- 永不物理删除，只归档
- 跳过固定技能和被 cron 任务引用的技能
- 支持 dry-run 模式预览

**伞形策略**：倾向于将相关技能合并为"类级别"的伞形技能，而不是维护扁平的窄技能列表。

#### 背景审查：对话后学习

每次对话结束后，一个轻量级的 Agent 分支会审查对话：

**记忆审查提示**：
```
审查上述对话，如果合适则保存到记忆。
关注：
1. 用户是否透露了关于自己的信息？
2. 用户是否表达了对你行为的期望？
```

**技能审查提示**：
```
审查上述对话并更新技能库。要积极——
大多数会话至少产生一次技能更新，即使很小。

寻找的信号：
  • 用户纠正了你的风格、语气、格式或详细程度
  • 用户纠正了你的工作流程或方法
  • 出现了非平凡的技术或解决方案
  • 加载的技能结果是错误或过时的
```

**优先级**：
1. 更新当前加载的技能（如果是 curator 管理的）
2. 更新现有的伞形技能
3. 添加支持文件（references/、templates/、scripts/）
4. 创建新技能（最后手段）

#### 技能溯源追踪

使用 ContextVar 追踪技能写入的来源：
- `background_review`：来自对话后审查的写入
- `curator`：来自生命周期管理的写入
- `user`：用户直接编辑
- `agent`：Agent 在正常对话中发起

启用守卫，如"背景审查只能写入 agent 创建的技能"。

---

### 🔄 记忆 + 技能协同

```
用户对话
    ↓
背景审查（对话后）
    ├─→ 记忆工具：保存事实到 MEMORY.md/USER.md/SOUL.md
    └─→ 技能管理器：创建/更新技能到 workspace/skills/
    
空闲期（30+ 分钟）
    ↓
Curator（每周）
    ├─→ 技能使用存储：检查 use_count、last_activity_at
    ├─→ 状态机：ACTIVE → STALE → ARCHIVED
    └─→ 伞形合并：整合相关技能
    
定时计划（每 2 小时）
    ↓
Dream
    ├─→ 读取会话消息（最近 20 个会话，100 条消息）
    ├─→ 使用 MECE 规则分类事实
    ├─→ 手术式编辑 SOUL.md/USER.md/MEMORY.md
    └─→ Git 提交，附带基于差异的提交信息
```

**数据流**：
1. **短期**：会话消息在 `session.messages` 中累积
2. **中期**：背景审查提取事实和技能
3. **长期**：Dream 整合到规范文件，Curator 管理技能生命周期

---

## 📦 安装

> [!IMPORTANT]
> 如果你想体验最新特性和实验，从源码安装。
>
> 如果你想要最稳定的日常体验，从 PyPI 安装或使用 `uv`。

选择**一种**安装方法：

前置条件：Python 3.11 或更新版本。仅源码安装需要 Git。发布的包已包含 WebUI；当前源码安装需要 `bun` 或 `npm` 来构建。

如果你对终端、API 密钥或配置文件不熟悉，请使用[无技术背景入门指南](./docs/start-without-technical-background.md)。

**一键安装**

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/chidao-cpu/nanobot-self-improve/main/scripts/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/chidao-cpu/nanobot-self-improve/main/scripts/install.ps1 | iex
```

默认命令从 PyPI 安装或升级 `nanobot-ai`。在全新的本地桌面上，它会启动 `nanobot webui`，让你可以在 **Settings → Models** 中配置第一个提供商和模型。SSH、无头、现有配置和旧版本路径保留终端设置向导。安装程序通过使用活动的虚拟环境、`uv`、`pipx` 或 `~/.nanobot/venv` 下的托管 venv 来避免系统级 pip 安装。它还会打印用于运行 nanobot 的确切命令；如果 `nanobot` 不在 `PATH` 中，请在下方重用该完整命令。

要预览计划而不更改环境，传递 `--dry-run`；当你想预览 main 分支安装时，将其与 `--dev` 结合使用。

```bash
curl -fsSL https://raw.githubusercontent.com/chidao-cpu/nanobot-self-improve/main/scripts/install.sh | sh -s -- --dry-run
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/chidao-cpu/nanobot-self-improve/main/scripts/install.ps1))) --dry-run
```

要安装当前的 `main` 分支，传递 `--dev`：

```bash
curl -fsSL https://raw.githubusercontent.com/chidao-cpu/nanobot-self-improve/main/scripts/install.sh | sh -s -- --dev
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/chidao-cpu/nanobot-self-improve/main/scripts/install.ps1))) --dev
```

如果你想先检查脚本，打开 [`scripts/install.sh`](./scripts/install.sh) 或 [`scripts/install.ps1`](./scripts/install.ps1)。

**使用 `uv` 安装**

```bash
uv tool install nanobot-ai
```

**使用 pip 从 PyPI 安装**

```bash
python -m pip install nanobot-ai
```

如果在 macOS 或 Linux 上 pip 报告 `externally-managed-environment`，使用一键安装器、`uv tool install nanobot-ai`、`pipx install nanobot-ai`，或在虚拟环境中安装。

**从源码安装**

`bun` 或 `npm` 必须可用。从激活的虚拟环境中：

```bash
git clone https://github.com/chidao-cpu/nanobot-self-improve.git
cd nanobot-self-improve
python -m pip install .
```

在 Windows 上，如果 pip 报告无法启动 `npm`，依次运行 `cd webui`、`npm.cmd install --package-lock=false`、`npm.cmd run build`、`cd ..`，然后重试安装。需要可编辑检出的贡献者应遵循 [`CONTRIBUTING.md`](./CONTRIBUTING.md) 和 [`webui/README.md`](./webui/README.md)。

验证安装：

```bash
nanobot --version
```

如果 `nanobot` 不在 `PATH` 中，通过安装它的方法调用：重用推荐安装器的命令，使用 `uv tool run --from nanobot-ai nanobot ...` 或 `pipx run --spec nanobot-ai nanobot ...`，或使用安装包的环境中的 Python 可执行文件。

---

## 🚀 快速开始

**在浏览器中打开 nanobot**

```bash
nanobot webui
```

这是推荐的首次运行方式。启动器在需要时创建配置和工作区，在确认后安全地启用本地 WebSocket 通道，启动网关，并打开 [`http://127.0.0.1:8765`](http://127.0.0.1:8765)。全新安装可以在配置模型之前打开，因此设置继续在浏览器中进行，而不是在 JSON 文件中开始。首次运行的 WebUI 默认绑定到 localhost，不会暴露到你的局域网。

**你的前三个步骤**

1. 打开 **Settings → Models**，选择提供商、凭证和模型。
2. 开始一个新主题，发送 `Hello!` 验证连接。
3. 在项目工作之前，从编辑器中选择预期的工作区和访问模式。

任何正常回复都意味着提供商、模型、工作区和浏览器网关正在协同工作。

**关闭终端后保持 nanobot 运行**

```bash
nanobot webui --background
```

这会启动与 `nanobot webui` 相同的完整网关，打开浏览器，并在启动器退出后保持通道和自动化运行。在切换到后台模式之前，使用前台 `nanobot webui` 完成首次模型设置。

```bash
nanobot gateway status
nanobot gateway logs
nanobot gateway restart
nanobot gateway stop
```

**偏好网关优先的工作流程？**

```bash
nanobot gateway
```

这会跳过 WebUI 设置和浏览器打开，然后在当前终端中运行相同的完整网关。如果你来自 OpenClaw 或已经将代理作为长期运行的服务操作，这是熟悉的入口点。当其通道配置时，WebUI 仍然可用；需要时手动打开它。

使用 `nanobot gateway --background` 获得相同的直接入口点，而无需保持终端连接。有关操作系统的自动启动和监督，请参阅[部署](./docs/deployment.md)。

**偏好完全在终端中工作？**

```bash
nanobot agent
```

这会打开一个交互式终端聊天，使用相同的配置模型、工作区和工具，同时保留自己的 CLI 会话历史。它不会打开浏览器或在退出后保持聊天通道和自动化运行。完成后输入 `exit` 或按 `Ctrl+C`。

对于单个请求和立即退出，使用：

```bash
nanobot agent -m "Hello!"
```

一次性形式对于快速提供商检查、shell 脚本和本地自动化很有用。如果你还没有配置模型，先运行 `nanobot webui` 并打开 **Settings → Models**。

需要手动 JSON、局域网上的其他设备或提供商/模型匹配的帮助？继续[安装和快速开始](./docs/quick-start.md)、[WebUI](./docs/webui.md) 或[故障排除](./docs/troubleshooting.md)。

如果 nanobot 对你有帮助，在 GitHub 上点赞是支持项目的最简单方式。

- 想要可粘贴的提供商设置？请参阅[提供商手册](./docs/provider-cookbook.md)
- 想要理解提供商/模型匹配？请参阅[提供商和模型](./docs/providers.md)
- 想要网络搜索、MCP、安全设置或更多配置选项？请参阅[配置](./docs/configuration.md)
- 想要本地运行？请参阅 [Ollama](./docs/providers.md#ollama)、[vLLM 或其他本地 OpenAI 兼容服务器](./docs/providers.md#vllm-or-other-local-openai-compatible-server) 和完整的[提供商参考](./docs/configuration.md#providers)。
- 想要在 Telegram、Discord、微信或飞书等聊天应用中运行 nanobot？请参阅[聊天应用](./docs/chat-apps.md)
- 想要 Docker 或 Linux 服务部署？请参阅[部署](./docs/deployment.md)

<a id="deploy-to-render"></a>

## ☁️ 部署

**Render — 一键部署**

从仓库的就绪蓝图部署 nanobot 的网关和捆绑的 WebUI：

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/chidao-cpu/nanobot-self-improve)

Render 会询问 `ANTHROPIC_API_KEY` 和私有的 `NANOBOT_WEB_TOKEN`，然后为会话、记忆和 WebUI 历史配置持久存储。持久磁盘需要付费的 Render 服务。

**自托管**

偏好自己的基础设施？遵循[部署指南](./docs/deployment.md)了解 Docker、Docker Compose、Linux 服务和 macOS LaunchAgent 设置。

---

## 🌐 WebUI

WebUI **在发布的 wheel 内部发布**，无需单独的前端构建。它是持久主题、可见代理活动、工作区控制、应用、技能、自动化和设置的浏览器工作台。

<p align="center">
  <img src="images/nanobot_webui.png" alt="nanobot webui preview" width="900">
</p>

使用它来：

- 为不同的任务和项目保持单独的主题；
- 检查推理、工具调用、文件编辑、差异、命令输出和生成的工件；
- 在不离开对话的情况下切换模型和工作区；
- 从一个地方配置提供商、聊天通道、应用、技能和自动化。

请参阅 [WebUI 指南](./docs/webui.md)了解局域网访问、后台操作、工作区控制和完整功能之旅。在前端本身上工作？使用 [`webui/README.md`](./webui/README.md)。

---

## 🏗️ 架构设计

<p align="center">
  <img src="images/nanobot_arch.png" alt="nanobot architecture" width="800">
</p>

🐈 nanobot 通过将一切围绕一个小型代理循环保持轻量：消息从聊天应用进入，LLM 决定何时需要工具，记忆或技能仅作为上下文拉入，而不是成为沉重的编排层。这保持了核心路径可读且易于扩展，同时仍然允许你添加通道、工具、记忆和部署选项，而不会将系统变成单体。

### 核心数据流

```
消息 → 通道 → MessageBus → AgentLoop → AgentRunner → LLM
                                                    ↓
                                              工具执行
                                                    ↓
                                        OutboundMessage → 通道
```

### 关键子系统

| 子系统 | 位置 | 职责 |
|--------|------|------|
| **Agent Loop** | `nanobot/agent/loop.py`, `runner.py` | 核心处理引擎，管理会话键、钩子和上下文构建 |
| **LLM Providers** | `nanobot/providers/` | 提供商实现（Anthropic、OpenAI、Azure、Bedrock 等） |
| **Channels** | `nanobot/channels/` | 平台集成（Telegram、Discord、Slack、飞书、微信等） |
| **Tools** | `nanobot/agent/tools/` | 代理能力：文件系统、shell、网络搜索、MCP、cron 等 |
| **Memory** | `nanobot/agent/memory.py` | 会话历史持久化，Dream 两阶段记忆整合 |
| **Session** | `nanobot/session/` | 每会话历史、上下文压缩、TTL 自动压缩 |
| **Skills** | `nanobot/skills/` | 内置技能定义，支持自进化 |
| **WebUI** | `webui/` | Vite React SPA，通过 WebSocket 多路复用协议与网关通信 |

---

## 📚 文档

浏览[仓库文档](./docs/README.md)了解最新特性和 GitHub 开发版本，或访问 [nanobot.wiki](https://nanobot.wiki/docs/latest/getting-started/nanobot-overview)了解稳定版文档。

- 使用任务导向指南：[指南](./docs/guides/README.md)
- 无技术背景开始：[无技术背景入门](./docs/start-without-technical-background.md)
- 从开发者基础开始：[安装和快速开始](./docs/quick-start.md)
- 理解运行时模型：[概念](./docs/concepts.md)
- 阅读源码级映射：[架构](./docs/architecture.md)
- 选择提供商/模型：[提供商和模型](./docs/providers.md)
- 复制提供商设置配方：[提供商手册](./docs/provider-cookbook.md)
- 调试设置和运行时故障：[故障排除](./docs/troubleshooting.md)
- 用熟悉的聊天应用与 nanobot 对话：[聊天应用 AI 代理](./docs/guides/chat-app-ai-agent.md) · [聊天应用](./docs/chat-apps.md)
- 计划或触发代理工作：[自动化](./docs/automations.md)
- 配置提供商、网络搜索、MCP 和运行时行为：[配置](./docs/configuration.md)
- 将 nanobot 与本地工具和自动化集成：[OpenAI 兼容 API](./docs/openai-api.md) · [Python SDK](./docs/python-sdk.md)
- 使用 Docker 或作为 Linux 服务运行 nanobot：[部署](./docs/deployment.md)

---

## 🔄 版本发布

**最新发布：[v0.3.0 - The Agency Release](https://github.com/HKUDS/nanobot/releases/tag/v0.3.0)**

Agency Release 将 nanobot 从持久工作台转变为能够协调助手、按会话切换模型并将授权工作 carried through to completion 的代理运行时。

- 在不离开当前任务的情况下咨询内联子代理
- 直接从编辑器按会话切换模型预设
- 从引导式 WebUI 设置开始，具有更清晰的执行控制
- 在更可靠的提供商、通道和工具运行时上实时应用配置更改

[阅读 v0.3.0 发布说明](https://github.com/HKUDS/nanobot/releases/tag/v0.3.0)

### 最近更新

- **2026-07-24** 引导式首次运行设置、内联子代理和从编辑器切换模型。
- **2026-07-23** Grok OAuth 与托管 X Search、实时图像设置和更清晰的后备模型。
- **2026-07-22** 并行搜索、实时配置重新加载、更丰富的应用发现和更流畅的移动 WebUI。
- **2026-07-21** Codex 快速模式、可见技能引用、更安全的配置保存和更稳定的任务清理。
- **2026-07-20** 更清晰的代码块和复制操作、自包含通道和更稳定的 QQ 重连。

有关旧更新，请参阅[发布存档](./docs/release-archive.md)或 [GitHub 发布](https://github.com/HKUDS/nanobot/releases)。

---

## 🤝 贡献

使用 nanobot 完成真实任务，报告损坏的内容，然后选择集中的改进。

- 阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)了解开发工作流程。
- 浏览[开放问题](https://github.com/chidao-cpu/nanobot-self-improve/issues)寻找要调查的问题。
- 打开[拉取请求](https://github.com/chidao-cpu/nanobot-self-improve/pulls)进行集中的修复或集成。

---

## 📄 许可证

本项目基于 MIT 许可证开源。详见 [LICENSE](./LICENSE)。

---

## 🙏 致谢

nanobot-self-improve 基于 [nanobot](https://github.com/HKUDS/nanobot) 项目，由 [Xubin Ren](https://github.com/re-bin) 创建。本仓库专注于记忆机制和技能自进化系统的增强。

感谢所有贡献者的辛勤工作！

<a href="https://github.com/HKUDS/nanobot/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=HKUDS/nanobot&max=100&columns=12&updated=20260210" alt="Contributors" />
</a>

<p align="center">
  <em>感谢访问 ✨ nanobot-self-improve！</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=chidao-cpu.nanobot-self-improve&style=for-the-badge&color=00d4ff" alt="Views">
</p>
