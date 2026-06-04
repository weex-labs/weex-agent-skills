# WEEX Agent Skills

[English](README.md)

本仓库目前支持 Codex、Openclaw、Claude Code 使用 WEEX Skills。

安装这些 skill 以后，你可以让 AI 工具查询 WEEX 市场数据、查看账户状态、采集交易历史、预览订单风险、创建自动化监控，或分析 WEEX 交易记录。普通使用不需要你直接运行 Python 脚本，从聊天里点名 skill 开始即可。

你可以把 skill 理解成 AI 工具的“能力包”。在聊天里提到 `$weex-trader-skill`、`$weex-analysis-skill` 或 `$weex-monitor-skill`，就是在告诉 AI 使用哪一套 WEEX 能力。

## 从这里开始

1. 推荐方式：直接让 AI 工具帮你安装：

```text
请从 https://github.com/weex-labs/weex-trader-skill 安装全部 WEEX Agent Skills。
```

如果你想手动安装，运行：

```bash
npx skills add https://github.com/weex-labs/weex-trader-skill --all
```

2. 安装完成后，在 AI 工具里点名你要用的 skill：

```text
使用 $weex-trader-skill 查询 BTCUSDT 最新现货价格。
```

3. 如果要查看私有账户或执行交易相关操作，skill 会引导你设置已保存的 WEEX API profile。这里的 profile 指本地保存的 API 凭证配置。优先使用本地 profile manager 或其他本地密钥输入方式，不要把密钥直接粘贴到聊天里。

4. 如果你是第一次使用，建议先从只读任务开始，比如查询行情、查看账户、采集交易历史复盘数据（replay），或分析风险，再尝试任何实时下单。

## 三个 Skill 分别做什么？

### `weex-trader-skill`

当 AI 工具需要连接 WEEX 时，使用 [`weex-trader-skill`](skills/weex-trader-skill/README.md)。

适合：

- 查询公开现货或合约市场数据
- 查看私有账户状态、余额、订单、仓位和订单状态
- 设置并使用已保存的 API profile
- 采集标准化交易历史，供后续分析使用
- 在实时交易前预览订单风险
- 在你明确确认后，下现货或合约订单，或撤销订单

示例提示词：

| 场景 | 提示词 |
|---|---|
| 查询价格 | `使用 $weex-trader-skill 查询 BTCUSDT 最新现货价格。` |
| 查看账户 | `使用 $weex-trader-skill 查看我的合约仓位和可用余额。` |
| 采集历史 | `使用 $weex-trader-skill 采集我最近 30 天的 BTCUSDT 合约交易历史复盘数据。` |
| 预览风险 | `使用 $weex-trader-skill 在开 BTCUSDT 多单前预览风险。` |
| 准备实时订单 | `使用 $weex-trader-skill 先预览一笔 200 USDT 的 BTC 市价买入，等我确认后再决定是否下单。` |

### `weex-analysis-skill`

当 AI 工具需要分析已经采集或导出的 WEEX 数据时，使用 [`weex-analysis-skill`](skills/weex-analysis-skill/README.md)。

这个 skill 是只读的。它不会连接你的实时私有账户，也不会下单或撤单。

适合：

- 审查敞口、集中度、杠杆和可用保证金
- 汇总成交记录、手续费和已实现盈亏（PnL）
- 分析 replay 行为和交易模式
- 根据标准化历史生成交易画像
- 审查由 `weex-trader-skill` 采集的订单风险或账户风险 JSON

示例提示词：

| 场景 | 提示词 |
|---|---|
| 审查敞口 | `使用 $weex-analysis-skill 分析这个 WEEX 账户快照，指出我的主要集中风险。` |
| 审查成交 | `使用 $weex-analysis-skill 审查这些成交记录，并汇总扣除手续费后的已实现盈亏。` |
| 审查行为 | `使用 $weex-analysis-skill 分析这份 replay 数据，并指出主要交易行为模式。` |
| 生成画像 | `使用 $weex-analysis-skill 根据这份 replay 数据生成交易画像。` |
| 审查账户风险 | `使用 $weex-analysis-skill 分析这个账户风险 JSON，并总结主要风险。` |

### `weex-monitor-skill`

当 AI 工具需要把自然语言 WEEX 监控指令整理成已确认的本地监控任务时，使用 [`weex-monitor-skill`](skills/weex-monitor-skill/SKILL.md)。

这个 skill 是本地仓位收益监控编排层。它负责起草、确认、存储、评估、通过 `weex-trader-skill` 执行并回报收益监控任务，但不保存 API 凭证、不解锁 vault、不签名、不直接提交 REST。收益触发后的市价平仓仍需要用户明确授权使用真实账户并提交真实平仓委托。价格条件平仓请改用 WEEX 官方条件单，并通过 `weex-trader-skill` 处理，不再由 `weex-monitor-skill` 创建本地价格监控。

适合：

- 按未实现盈亏监控单个合约仓位
- 当收益阈值触发且用户授权真实账户执行时，通过 `weex-trader-skill` 执行方向级市价平仓
- 使用本地仓位快照做 dry-run 监控演练
- 查看、审计和取消本地监控任务

示例提示词：

| 场景 | 提示词 |
|---|---|
| 收益监控 | `使用 $weex-monitor-skill 监控 BTCUSDT 多单，先核对真实持仓，未实现盈利大于 50 USDT 时在我授权后市价平多。` |
| 查看监控 | `使用 $weex-monitor-skill 列出我的本地监控任务和最近事件。` |

## 我应该用哪个 Skill？

| 你想做什么 | 使用 |
|---|---|
| 查询实时行情价格 | `weex-trader-skill` |
| 查询实时私有账户、余额、订单或仓位数据 | `weex-trader-skill` |
| 设置或使用已保存的 WEEX API profile | `weex-trader-skill` |
| 预览、下单、撤单或检查实时订单 | `weex-trader-skill` |
| 创建或查看收益条件的本地自动化监控 | `weex-monitor-skill` |
| 创建交易所原生价格条件平仓 | `weex-trader-skill` |
| 分析已有的 WEEX JSON 文件或粘贴的 JSON 数据 | `weex-analysis-skill` |
| 分析实时账户历史 | 先用 `weex-trader-skill` 采集数据，再用 `weex-analysis-skill` 分析 |

## 从本地目录安装（可选）

如果你已经下载或 clone 了本仓库，并想从这个本地目录安装，运行：

```bash
python3 tools/install_local_skills.py --all --agent codex
```

Claude Code 请使用 `--agent claude-code`。本地安装工具会校验 `gh skill install` 当前支持的 agent；如果你的宿主不在支持列表里，请用 `--dir` 安装到该宿主期望的 skills 目录。

`weex-monitor-skill` 的实时账户读取和实时执行委托依赖 `weex-trader-skill`。从本地安装器单独安装 `weex-monitor-skill` 时会自动带上 `weex-trader-skill`；普通使用仍建议安装全部 skills。

大多数用户只需要使用 [从这里开始](#从这里开始) 中的 GitHub 安装命令。

## 使用前请注意

- 实时下单、撤单或修改账户状态的操作会影响真实资产。确认前请核对账户、交易对、方向、数量、价格、订单类型和风险预览。
- 不要把 API key、API secret、passphrase、vault password 或临时密钥文件粘贴到聊天窗口、issue、公开日志或截图里。
- 优先使用 saved profile、本地 profile manager、`--prompt-secrets`、环境变量或 `--secrets-stdin-json` 等本地密钥输入方式。
- 为这个工作流使用最小权限 API key。如果凭证可能已经暴露，请立即撤销或轮换。
- `weex-analysis-skill` 的输出只用于复盘和风险参考，不构成投资或交易建议。
- 不确定时，先让 AI 工具预览或解释，再要求它执行任何操作。

## 更多文档

- [`weex-trader-skill` README](skills/weex-trader-skill/README.md)：实时 WEEX 访问、API profile、订单预览、实时订单流程和故障排查。
- [`weex-analysis-skill` README](skills/weex-analysis-skill/README.md)：输入数据要求、分析示例、replay 复盘和安全说明。
- [`weex-monitor-skill` SKILL.md](skills/weex-monitor-skill/SKILL.md)：自动化监控 DSL、确认流程、dry-run runner 和 live 执行边界。
- [`weex-trader-skill` script operations](skills/weex-trader-skill/references/script-operations.md)：面向进阶用户的直接脚本用法。
- [`weex-analysis-skill` analysis playbook](skills/weex-analysis-skill/references/analysis-playbook.md)：分析行为和结果解读细节。
