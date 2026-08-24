# WEEX Agent Skills

[English](README.md)

本仓库为 Codex、Claude Code、Cursor、GitHub Copilot 和 OpenClaw 提供 WEEX Skills 安装入口。前四个宿主使用本地安装器；OpenClaw 使用固定的本地 Git 仓库和 skill 目录软链接，并由仓库内更新脚本统一维护。

安装这些 skill 以后，你可以让 AI 工具查询 WEEX 市场数据、查看账户状态、采集交易历史、预览订单风险、创建自动化监控，或分析 WEEX 交易记录。普通使用不需要你直接运行 Python 脚本，从聊天里点名 skill 开始即可。

你可以把 skill 理解成 AI 工具的“能力包”。在聊天里提到 `$weex-trader-skill`、`$weex-analysis-skill`、`$weex-monitor-skill` 或 `$weex-partner-skill`，就是在告诉 AI 使用哪一套 WEEX 能力。

## 从这里开始

1. 推荐方式：直接让 AI 工具帮你安装：

```text
请从 https://github.com/weex-labs/weex-agent-skills 安装全部 WEEX Agent Skills。
```

如果你想手动安装，运行：

```bash
npx skills add https://github.com/weex-labs/weex-agent-skills --all
```

OpenClaw 用户不要使用上面的 `npx` 命令，请改用下方的 [Git 仓库与软链接流程](#安装或更新-openclaw)。

2. 安装完成后，在 AI 工具里点名你要用的 skill：

```text
使用 $weex-trader-skill 查询 BTCUSDT 最新现货价格。
```

3. 如果要查看私有账户或执行交易相关操作，skill 会引导你设置已保存的 WEEX API profile。这里的 profile 指本地保存的 API 凭证配置。优先使用本地 profile manager 或其他本地密钥输入方式，不要把密钥直接粘贴到聊天里。

4. 如果你是第一次使用，建议先从只读任务开始，比如查询行情、查看账户、采集交易历史复盘数据（replay），或分析风险，再尝试任何实时下单。

## 四个 Skill 分别做什么？

### `weex-trader-skill`

当 AI 工具需要连接 WEEX 时，使用 [`weex-trader-skill`](skills/weex-trader-skill/README.md)。

适合：

- 查询公开现货或合约市场数据
- 查看私有账户状态、余额、订单、仓位和订单状态
- 设置并使用已保存的 API profile
- 采集标准化交易历史，供后续分析使用
- 在实时交易前预览订单风险
- 为保存的策略注册有限期、可撤销、按额度控制且可审计的自动交易授权
- 在你明确确认后，下现货或合约订单，或撤销订单

示例提示词：

| 场景 | 提示词 |
|---|---|
| 查询价格 | `使用 $weex-trader-skill 查询 BTCUSDT 最新现货价格。` |
| 查看账户 | `使用 $weex-trader-skill 查看我的合约仓位和可用余额。` |
| 采集历史 | `使用 $weex-trader-skill 采集我最近 30 天的 BTCUSDT 合约交易历史复盘数据。` |
| 预览风险 | `使用 $weex-trader-skill 在开 BTCUSDT 多单前预览风险。` |
| 准备实时订单 | `使用 $weex-trader-skill 先预览一笔 200 USDT 的 BTC 市价买入，等我确认后再决定是否下单。` |
| 配置自动交易授权 | `使用 $weex-trader-skill 为我的策略申请 24 小时现货和合约自动交易授权，单笔 200 U、累计 2000 U。` |

### 自动策略授权

正式版 Trader 支持用户自己维护的 Python/量化策略显式接入，不会自动识别或包装任意脚本；自动交易授权只支持 saved profile 的真实盘，不支持模拟盘。策略需要先注册稳定身份，在首次下单前请求自己的授权。重启或改名后继续复用该身份；复制策略会得到新身份，并拥有独立的授权和额度。授权范围只有五个维度：现货/合约模块、指定交易对或全部交易对、单个 leg 的保守 U 估算上限、有效期内累计 U 估算上限，以及明确的有效期；不额外限制买卖方向、订单类型、最小下单金额或订单次数。自然语言请求缺少有效期时必须先向用户确认；累计额度未明确时也必须暂停并单独询问，不得根据频率、有效期、目标金额、单笔上限、余额或预计笔数推算 `max_total_amount`；最终确认中的累计额度必须与用户输入一致。JSON CLI 的 `valid_hours` 为必填项，必须大于 0 且不能超过 720 小时（30 天）。授权必须绑定原始申请并使用 `--confirm-live` 才能生效。待确认申请本身没有交易权限，并会在 15 分钟后失效；授权有效期从批准成功时开始计算。修改任一授权维度都需要重新申请并明确授权，新授权生效后会替换该策略原有的活动授权。

真实盘自动任务只有在同一次原子更新中同时写入 `ACTIVE` 和不晚于授权到期时间的 `UNTIL` 后才能运行；不得先激活无到期时间的循环。如果运行时不能原子更新状态和到期时间，必须保持 `PAUSED`。

用户确认授权时需要理解：

- 授权只会修改本地授权状态，本身不会向 WEEX 提交订单。
- 授权有效期间，符合范围且通过检查的订单可以不再逐笔确认；每笔订单仍必须通过官方数据、风险、产品规则、余额、范围和额度检查。
- 单个 leg 和累计额度约束的是保守 U 估算值，不是请求金额或实际成交金额。订单一旦被接受，其估算额度会在本次授权期内持续占用，后续对账不会返还。
- 授权过期或撤销只会阻止未来的自动预占；停用策略还会永久阻止该策略身份再次申请授权。这些操作不会取消、重试或修改已经提交到 WEEX 的订单。

只有官方现货/合约操作且官方数据新鲜、完整时才能进入自动路径。批量 leg 先整组原子校验和预占，再记录策略、授权、usage、提交组、client order ID 与 WEEX order ID。订单被 WEEX 明确接受后，保守估算额度不会因后续对账返还；明确拒绝会释放预占；结果或映射不确定时转人工且不重试。全仓 TP/SL、无法证明的只减仓语义、陈旧/降级数据、缺少汇率/深度/杠杆/费率、超范围/超额、撤销或过期授权、状态冲突和未知操作都不会自动下单。

授权生命周期、`submit-auto`、恢复、事件和官方只读对账统一使用 `skills/weex-trader-skill/scripts/weex_auto_trade.py` 的 saved-profile JSON facade；策略通过子进程调用该 CLI，不直接 import 状态内核，也不能注入生产 risk/fact/submit 实现。原始凭据和直接写数据库都会被拒绝。本地 owner-only 权限、完整性检查和迁移只用于降低误用/损坏风险，不是身份认证，也不能防御控制同一 OS 用户、Agent 或 Vault 会话的攻击者。普通成功通知可以按 60 秒聚合，异常通知立即发送且只尝试一次。完整 JSON 命令见 [脚本操作说明](skills/weex-trader-skill/references/script-operations.md)。

同一个 facade 支持用户显式触发的 owner-only 本地快照，默认保留 10 份（范围 1-100），并只按 Trader 生成的 snapshot ID 恢复。任何语法有效的恢复尝试都会在读取索引前先开启持久 kill switch，因此未知 ID、损坏索引或无效快照也会保持自动交易关闭。恢复会保留当前数据库证据和未决 usage；必须重新完成晚于 kill switch 的详细授权，用可靠证据通过 `resolve-auto-usage` 处理全部未决记录，再显式执行 `enable-auto-trading-after-restore --confirm-live`。快照不提供额外密码加密，也不会自动上传或跨设备同步。自动授权状态目前在 Windows 上 fail-closed，直到实现并验证 owner-only DACL；Trader 其他 Windows 流程不受影响。

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

### `weex-partner-skill`

查询 WEEX 合伙人直客 UID、直客交易与资金、返佣、子代理统计、直客关系、直客资产或交易统计时，使用 [`weex-partner-skill`](skills/weex-partner-skill/SKILL.md)。

它复用 `weex-trader-skill` 的现有 profile 和 Application Vault，REST、凭据和签名仍由 trader 负责；同一个 profile 仍可通过 trader 的既有预览和明确确认门禁正常下单。

Partner-only 对话使用 trader 的安全 Partner preflight：只返回选定 profile 的非秘密身份与 `partner_production|partner_test` 标签，不把具体测试地址、API key hint 或其他 profile 路由元数据写入工具 transcript。

示例：`使用 $weex-partner-skill 查询官方默认时间范围内的返佣，并说明实际 UTC 时间范围。`

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
| 查询合伙人直客、返佣、资产或子代理数据 | `weex-partner-skill` |

## 从本地目录安装（可选）

如果你已经下载或 clone 了本仓库，并想从这个本地目录安装，运行：

```bash
python3 tools/install_local_skills.py --all --agent codex
```

Claude Code、Cursor、GitHub Copilot 分别使用 `--agent claude-code`、`--agent cursor`、`--agent github-copilot`。本地安装工具会校验 `gh skill install` 当前支持的 agent。

`weex-monitor-skill` 和 `weex-partner-skill` 都依赖 `weex-trader-skill`。从本地安装器单独安装任意一个时会自动带上 trader；普通使用仍建议安装全部 skills。

### 安装或更新 OpenClaw

OpenClaw 统一保留一个固定 Git 仓库，并通过软链接暴露每个 skill。默认目录结构如下：

- 仓库：`~/.openclaw/skill-repos/weex-agent-skills`
- `~/.openclaw/skills/weex-trader-skill`
- `~/.openclaw/skills/weex-analysis-skill`
- `~/.openclaw/skills/weex-monitor-skill`
- `~/.openclaw/skills/weex-partner-skill`

在包含本版本代码的仓库目录中运行：

```bash
bash skills/weex-trader-skill/scripts/update_openclaw_skills.sh
```

脚本会从官方仓库取得批准的固定 release commit `e10c2089550159afb7247271d1041d9b415145cd`，生产模式不会跟随可变分支。它先在临时目录校验 Git 对象完整性、四个 skill 和无 submodule，再切换软链接并运行 OpenClaw 检查；稳定更新副本保存在 `~/.openclaw/update-weex-openclaw-skills.sh`，拉取的 checkout 不能覆盖更新入口。任一步失败都会恢复旧 checkout 和旧链接。

```bash
openclaw skills list --eligible
openclaw skills info weex-trader-skill
openclaw skills check
```

以后更新只需要运行：

```bash
~/bin/update-weex-openclaw-skills.sh
```

如果链接目标位置已经存在真实文件或目录，脚本会停止，不会覆盖用户数据；请人工处理冲突后重试。其他仓库或分支只允许显式开发模式：

```bash
WEEX_OPENCLAW_REPO_URL=/path/to/checkout \
WEEX_OPENCLAW_BRANCH=feature/my-branch \
bash skills/weex-trader-skill/scripts/update_openclaw_skills.sh --dev
```

`WEEX_OPENCLAW_REPO_DIR`、`WEEX_OPENCLAW_SKILLS_DIR` 和 `WEEX_OPENCLAW_BIN_LINK` 仍可用于选择本地安装目录。

校验失败时脚本已经自动保留旧 checkout 和旧链接；需要再次回滚或恢复时，重新运行稳定更新入口：

```bash
bash ~/.openclaw/update-weex-openclaw-skills.sh
```

最后新建一个 OpenClaw 任务，执行只读 smoke test：

```text
使用 $weex-trader-skill 查询 BTCUSDT 最新现货价格。
```

不要使用 `gh skill install --agent openclaw`；根目录 Python 安装器继续只服务其他受支持宿主。

Codex、Claude Code、Cursor 或 GitHub Copilot 用户通常只需要使用 [从这里开始](#从这里开始) 中的 GitHub 安装命令；OpenClaw 用户继续使用上面的专用流程。

## 使用前请注意

- 实时下单、撤单或修改账户状态的操作会影响真实资产。确认前请核对账户、交易对、方向、数量、价格、订单类型和风险预览。
- 不要把 API key、API secret、passphrase、vault password 或临时密钥文件粘贴到聊天窗口、issue、公开日志或截图里。
- 优先使用 saved profile、本地 profile manager、`--prompt-secrets`、环境变量或 `--secrets-stdin-json` 等本地密钥输入方式。
- 为这个工作流使用最小权限 API key。如果凭证可能已经暴露，请立即撤销或轮换。
- Partner 查询使用生产默认地址，或 saved profile 中的 HTTPS `https://*.weex.tech` 测试子域。结果只显示 Partner 环境标签，不显示具体测试地址；仍禁止 API base 环境变量覆盖和鉴权重定向。
- `weex-analysis-skill` 的输出只用于复盘和风险参考，不构成投资或交易建议。
- 不确定时，先让 AI 工具预览或解释，再要求它执行任何操作。

## 更多文档

- [`weex-trader-skill` README](skills/weex-trader-skill/README.md)：实时 WEEX 访问、API profile、订单预览、实时订单流程和故障排查。
- [`weex-analysis-skill` README](skills/weex-analysis-skill/README.md)：输入数据要求、分析示例、replay 复盘和安全说明。
- [`weex-monitor-skill` SKILL.md](skills/weex-monitor-skill/SKILL.md)：自动化监控 DSL、确认流程、dry-run runner 和 live 执行边界。
- [`weex-partner-skill` SKILL.md](skills/weex-partner-skill/SKILL.md)：合伙人查询路由、全量确认、默认时间、分页和只读边界。
- [`weex-trader-skill` script operations](skills/weex-trader-skill/references/script-operations.md)：面向进阶用户的直接脚本用法。
- [`weex-analysis-skill` analysis playbook](skills/weex-analysis-skill/references/analysis-playbook.md)：分析行为和结果解读细节。
