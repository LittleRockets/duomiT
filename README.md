<div align="center">

<p>
  <img src="./duomit-svg.svg" alt="duomiT 多面体 — AI 智能组织工作流与执行中间件" width="100%" />
</p>

# <img src="./duomit-icon-svg.svg" alt="" width="36" height="36" /> duomiT

**面向智能组织的 AI 工作流与执行中间件**

连接用户目标、模型推理与可追溯的执行过程。

[功能特性](#功能特性) · [快速开始](#快速开始) · [模型接入](#模型接入) · [开发验证](#开发验证) · [许可证](#许可证)

Python 3.11+ · Apache License 2.0 · Early Development

</div>

---

## 项目概述

duomiT 围绕任务规划、独立工作流与人机协作构建智能组织的执行基础。Builder 将用户目标转换为结构化计划，运行时维护执行状态、审批记录与恢复过程，使模型推理与实际操作之间具备明确的责任边界。

系统采用模块化接口设计。规划模型与助理模型独立配置，模型输出经过契约与能力校验后进入工作流；模型本身不授予权限，也不直接改变执行状态。

> 当前版本处于早期开发阶段，提供 API 适配器与本地工作流验证环境。真实模型服务尚未完成联调，工具执行仍使用模拟执行器。

## 功能特性

| 功能 | 当前支持 |
| --- | --- |
| 结构化规划 | JSON Schema、依赖图校验、循环依赖检测、能力范围校验 |
| LLM API 适配 | GPT / OpenAI、Gemini、DeepSeek、Grok / xAI |
| 独立助理接口 | 独立模型绑定、显式会话历史、文本建议与复核 |
| 模型配置 | 按用户与角色持久化、历史版本、并发更新校验、停用控制 |
| 持久化工作流 | SQLite 状态存储、幂等创建、模型与能力版本记录、执行恢复 |
| 人工审批 | 操作绑定、版本校验、有效期校验 |
| 取消与异常核对 | 停止后续步骤，对结果不确定的操作保留核对状态 |
| 事件记录 | 状态与事件事务写入、事件重放与投递确认 |
| 访问边界 | 本地访问令牌验证、用户与租户归属校验 |

## 设计原则

- **接口驱动**：模型、规划策略与执行器通过独立契约连接。
- **上下文隔离**：用户绑定、任务状态与会话历史具有明确归属。
- **权限受控**：计划生成与操作授权分离，强制审批不能被模型移除。
- **执行可恢复**：先记录状态再执行操作，未知结果须核对后处理。
- **证据可追溯**：运行、步骤、审批与事件通过标识和版本关联。

## 快速开始

需要 Python 3.11 或更高版本，当前运行时无第三方依赖。以下示例使用 PowerShell。

```powershell
git clone https://github.com/LittleRockets/duomiT.git
cd duomiT
$env:PYTHONPATH = "src"
```

### 生成离线示例计划

```powershell
python -m duomit --model sequential
python -m duomit --model independent
```

这两个模型为确定性的测试替身，不加载模型权重，不产生 API 费用。

### 运行持久化示例

```powershell
python -m duomit --database demo.sqlite3
python -m duomit --database demo.sqlite3 --resume --approve
```

首次执行在人工审批步骤暂停，第二次显式批准后继续。恢复复用已保存的计划，不再次调用规划模型。示例使用模拟执行器，不调用真实工具或生成实际报告；创建不同计划时应使用新的数据库。

### 本地身份验证

本地管理端可通过 `LocalIdentityStore.issue()` 签发有期限的访问令牌，并将令牌设置到当前会话的 `DUOMIT_ACCESS_TOKEN` 环境变量。命令行根据令牌记录派生用户与租户身份：

```powershell
python -m duomit --identity-database identities.sqlite3 --config-database settings.sqlite3 --ask "梳理这项需求"
```

此示例要求该用户已保存助理模型配置。身份数据库仅保存令牌摘要，支持过期与撤销；签发和撤销接口供可信本地管理端调用。未指定身份数据库时使用固定演示身份。

## 模型接入

真实 LLM 统一通过 API 接入，具体模型 ID 按账号可用模型配置。

| 服务 | 协议 | 默认密钥环境变量 |
| --- | --- | --- |
| GPT / OpenAI | Responses | `OPENAI_API_KEY` |
| Gemini | generateContent | `GEMINI_API_KEY` |
| DeepSeek | Chat Completions | `DEEPSEEK_API_KEY` |
| Grok / xAI | Responses | `XAI_API_KEY` |

协议参考：[OpenAI](https://developers.openai.com/api/reference/resources/responses/methods/create) · [Gemini](https://ai.google.dev/api/generate-content) · [DeepSeek](https://api-docs.deepseek.com/api/create-chat-completion/) · [xAI](https://docs.x.ai/developers/rest-api-reference/inference/responses)。

### 规划模型

在当前会话设置对应密钥环境变量后执行：

```powershell
python -m duomit --provider openai --model-id "<模型 ID>" --goal "生成数据报告计划"
```

生成的计划仍需通过结构、依赖、能力与审批校验。使用 `--database` 可保存模拟运行状态。

### 助理模型

```powershell
python -m duomit --assistant-provider gemini --assistant-model-id "<模型 ID>" --ask "梳理这项需求的验收条件"
```

规划与助理角色均支持上述四家服务，可分别选择服务商、模型和密钥。未配置的角色不会自动借用另一角色的模型或上下文。助理返回文本建议，不批准操作或启动工作流。

### 保存模型配置

```powershell
python -m duomit --config-database settings.sqlite3 --save-config --assistant-provider gemini --assistant-model-id "<模型 ID>"
python -m duomit --config-database settings.sqlite3 --ask "梳理这项需求"
```

保存操作不调用模型。配置数据库记录模型选择和密钥环境变量名称，不保存密钥值。规划模型同样可通过 `--save-config --provider ... --model-id ...` 保存；后续读取配置时无需重复传入模型参数。

使用 `--api-key-env` 与 `--assistant-api-key-env` 可分别指定密钥环境变量名称。密钥值不应写入命令参数、配置文件或源代码。API 调用可能产生费用，用量与数据保留规则以对应服务商为准。

## 开发验证

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m duomit --help
```

测试覆盖契约校验、API 协议映射、角色与用户隔离、审批有效期、幂等行为、中断核对、并发领取、数据库迁移及事务事件一致性。模型请求通过模拟 API 验证，不依赖真实服务密钥。

## 当前边界

- 命令行支持固定演示身份或本地访问令牌，尚未提供生产认证服务、操作系统级沙盒或真实工具执行环境。
- API 接入支持官方 HTTPS 端点和非流式文本，不自动使用系统代理或自定义中转服务。
- 请求失败不自动重试，也不自动切换服务商；凭据仅通过请求头发送。
- 超时针对阻塞 socket 操作，尚不提供覆盖 DNS 解析的严格整体截止时间。
- 中断核对需由可信操作方确认旧 worker 已停止；当前未提供分布式故障转移。

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。
