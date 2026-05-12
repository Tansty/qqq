# QQQ Advisor Agent 说明

`qqq_agent.py` 是本项目的自动运行和自动进化入口。它负责每天生成建议、记录历史、复盘表现，并在样本足够时尝试优化模型参数。

## 1. Agent 目标

Agent 的目标是让模型形成闭环：

```text
生成建议 -> 本地记录 -> 等待未来行情 -> 复盘准确率 -> 生成候选参数 -> 回测候选参数 -> 达标后写入新参数
```

它不会直接修改 Python 代码，而是修改本地 JSON 参数文件：

```text
data/model_params.json
```

这样每次进化都有记录、可回滚、可比较。

## 2. 本地 JSON 数据

| 文件 | 作用 |
|---|---|
| `data/model_params.json` | 当前生效的模型参数 |
| `data/macro_context.json` | 国际局势、政治、经济和大事件的结构化摘要 |
| `data/advice_log.json` | 每日建议历史 |
| `data/manual_orders.json` | 待手动确认交易单 |
| `data/actual_trades.json` | 手动确认后的真实成交 |
| `data/evolution_log.json` | 每次进化尝试记录 |
| `data/agent_state.json` | Agent 最近一次运行状态 |
| `data/report-YYYY-MM-DD.json` | 每日完整报告 |

`data/` 已加入 `.gitignore`，这些文件只保存在本地。

## 3. Qwen 配置

Qwen 只用于生成复盘分析文字和辅助解释。参数是否真正应用，仍由本地回测门槛决定。

不要把 API key 写入代码或文档。建议使用环境变量：

```bash
export QWEN_API_KEY="你的新key"
export QWEN_MODEL="qwen3.6-plus-2026-04-02"
export QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
```

也可以复制 `.env.example` 为 `.env`，但运行前需要手动加载：

```bash
set -a
source .env
set +a
```

你之前发在对话里的 key 已经暴露过，建议在平台重新生成一个新 key。

## 4. Agent 命令

每日运行：生成建议、记录日志、复盘并尝试进化。

```bash
python3 qqq_agent.py daily
```

只做复盘和参数进化：

```bash
python3 qqq_agent.py evolve
```

查看 Agent 状态：

```bash
python3 qqq_agent.py status
```

用最近 100 个可标注交易日训练：

```bash
python3 qqq_agent.py train --lookback 100
```

让 Qwen 参与提出候选因子：

```bash
python3 qqq_agent.py train --lookback 100 --use-qwen-factors
```

冲击指定验证集准确率，例如 80%：

```bash
python3 qqq_agent.py train --lookback 100 --optimize accuracy --target-accuracy 80 --search-iterations 1200 --use-qwen-factors
```

只训练评估、不写入新参数：

```bash
python3 qqq_agent.py train --lookback 100 --no-apply
```

完全不调用 Qwen，只用本地规则：

```bash
python3 qqq_agent.py daily --no-qwen
python3 qqq_agent.py evolve --no-qwen
```

## 4.1 支付宝交易边界

Agent 不会自动登录支付宝，也不会自动点击申购、赎回或确认交易。

当模型生成买入或卖出建议时，系统只会写入：

```text
data/manual_orders.json
```

你需要打开支付宝，手动核对基金代码、金额、费率、限购和确认日后再下单。这个设计是有意保留人工确认，避免程序自动动用资金。

成交后再把真实交易写入：

```text
data/actual_trades.json
```

这样 Agent 可以分开评估“建议质量”和“真实执行质量”。

## 5. 自动进化规则

当前进化逻辑是保守的：

1. 至少需要 `min_completed_20d_for_evolution` 条具备 20 个交易日复盘结果的样本，默认是 `5`。
2. Agent 根据历史样本生成一个候选参数。
3. 使用同一批历史样本对当前参数和候选参数做 20 日复盘。
4. 只有候选参数准确率高于当前参数时，才会写入 `data/model_params.json`。
5. 每次尝试都会写入 `data/evolution_log.json`。

## 5.0 因子增删和权重探索

模型因子保存在：

```text
data/model_params.json
```

路径：

```text
factor_model.factors
```

每个因子可以：

- 用 `enabled` 启用或关闭。
- 用 `weight` 调整权重。
- 用 `min_weight` / `max_weight` 限制 Agent 探索范围。
- 直接删除因子。
- 手动新增符合支持类型的新因子。

Agent 会尝试小步调整因子权重，并用历史复盘判断是否更好。只有回测表现提升才会应用。

训练样本现在会包含真实外部市场因子：

- VIX: `^VIX`
- 美债10年收益率: `^TNX`
- DXY: `DX-Y.NYB`
- QQQ 成交量和近20日成交量变化

这些外部因子会参与候选因子搜索和 Qwen 因子提案。为了避免刚接入的新数据源把模型拉偏，它们默认不会直接启用；只有本地回测达到更好的验证效果时，Agent 才会把对应因子的 `enabled` 写成 `true`。

训练日志保存在：

```text
data/training_log.json
```

训练不会写入 `data/advice_log.json`，不会污染每日真实建议记录。

## 5.1 宏观事件输入

Agent 会读取：

```text
data/macro_context.json
```

该文件只保存结构化摘要，不保存新闻全文。示例：

```json
{
  "updated_at": "2026-05-10",
  "source": "manual_local_json",
  "events": [
    {
      "category": "monetary_policy",
      "direction": "risk_off",
      "severity": 3,
      "summary": "美联储释放更久维持高利率的信号，成长股估值承压",
      "expires_at": "2026-05-20"
    }
  ]
}
```

宏观事件会先在本地规则里折算成评分调整；再以压缩形式传给 Qwen 辅助解释。

还可以在同一个文件里维护汇率和央行信息：

```json
{
  "currency": {
    "usdcny": 7.2,
    "usdcny_change_pct_30d": 1.5,
    "cny_direction": "depreciating"
  },
  "central_banks": {
    "global_bias": "hiking"
  },
  "usd_liquidity": "tightening"
}
```

## 6. 当前候选参数调整方向

如果多次出现：

```text
暂不买入 -> 20日后上涨超过3% -> 当时 RSI >= 70
```

Agent 会认为模型可能过度惩罚强趋势高 RSI，于是候选参数会：

- 降低 RSI 过热扣分
- 降低小额试探门槛
- 略微提高小额试探买入比例

如果多次出现：

```text
建议买入 -> 20日后下跌超过3%
```

Agent 会认为模型可能买入太激进，于是候选参数会：

- 提高正常买入和加仓门槛
- 降低小额试探和正常买入比例

## 7. 为什么不让大模型直接改参数

投资模型不能让大模型“凭感觉”直接改。当前设计把职责分开：

| 模块 | 职责 |
|---|---|
| 本地规则 | 计算评分、生成建议、判断是否应用新参数 |
| 本地 JSON | 保存参数、历史和进化记录 |
| Qwen | 分析复盘结果、解释候选调整 |

最终是否应用新参数，由本地回测结果决定。

## 7.1 Token 精简

传给 Qwen 的内容是压缩 JSON：

- 复盘只传摘要和最近最多 8 条样本。
- 宏观事件最多传 5 条。
- 每条事件摘要截断到约 90 个字符。
- 参数只传变化 diff，不传完整参数文件。
- 不传长新闻全文、不传完整历史日志。

这可以让 Qwen 参考国际局势、政治、经济和大事件，同时控制 token 成本。

## 8. 推荐定时任务

Docker Compose 云服务器部署时，建议让宿主机 cron 调用容器内任务：

```bash
./scripts/install_cron_daily.sh
```

默认安装：

```cron
0 7 * * 2-6 cd 项目目录 && ./scripts/run_daily_docker.sh # qqq-advisor-daily
```

日志写入：

```text
storage/logs/daily-YYYYMMDD.log
```

裸机运行时，中国时区建议每天早上 `07:00` 以后运行：

```bash
cd /Users/dz0401012/Desktop/pythonProject/qqq && python3 qqq_agent.py daily
```

如果暂时不用 Qwen：

```bash
cd /Users/dz0401012/Desktop/pythonProject/qqq && python3 qqq_agent.py daily --no-qwen
```
