# QQQ / 纳斯达克100基金入场助手

这是一个面向国内基金投资者的本地网页工具，用来每日追踪 `QQQ`，根据趋势、回撤、RSI、目标仓位和你的资产配置，输出“今天买不买、买多少、候选支付宝基金”的建议，并持续记录历史建议用于复盘优化。

它是投资辅助工具，不是投顾服务。最终下单前必须以支付宝基金页面的申购状态、限购、费率、确认日和赎回规则为准。

## 项目文件

| 文件 | 作用 |
|---|---|
| `web_server.py` | 本地网页服务和 API |
| `qqq_advisor.py` | 行情获取、评分、建议、记录和复盘逻辑 |
| `static/index.html` | 网页端输入和展示界面 |
| `config.example.json` | 可提交到 Git 的配置模板 |
| `config.json` | 你的资金、风险偏好和当前持仓配置；默认不提交 |
| `data/macro_context.json` | 国际局势、政治、经济和大事件摘要；默认不提交 |
| `data/report-YYYY-MM-DD.json` | 每日完整建议报告；默认不提交 |
| `data/advice_log.json` | 每日建议历史记录；默认不提交 |
| `data/manual_orders.json` | 待手动确认的支付宝交易单；默认不提交 |
| `data/actual_trades.json` | 你手动确认后的真实成交记录；默认不提交 |
| `MODEL.md` | 模型规则、评分原理、复盘逻辑和优化方法 |
| `AGENT.md` | 自动进化 agent 的运行方式、Qwen 配置和安全边界 |

## 快速启动

进入项目目录：

```bash
cd /Users/dz0401012/Desktop/pythonProject/qqq
```

启动网页端：

```bash
./start_web.sh
```

浏览器打开：

```text
http://127.0.0.1:8765
```

如果想改端口：

```bash
QQQ_ADVISOR_PORT=8766 ./start_web.sh
```

## 云服务器部署

如果要放到云服务器上长期自用，推荐用 Docker Compose 部署，并把所有可变数据放在宿主机的 `storage/` 目录。这样应用镜像可以随时重建，真正需要保护和迁移的是 `storage/`。

### 一键部署

在 Linux 云服务器上 clone 仓库后执行：

```bash
./deploy.sh
```

这个脚本会自动完成：

- 安装 Docker 和 Docker Compose 插件。
- 兼容 Ubuntu/Debian、CentOS/RHEL/Fedora、Amazon Linux 2/2023。
- 安装 cron。
- 生成 `.env` 和随机网页登录密码。
- 初始化 `storage/config.json` 与 `storage/data/`。
- 构建并启动 `qqq-advisor` 容器。
- 安装每日定时任务。

默认访问地址：

```text
http://你的服务器IP:8765
```

默认定时任务是中国时区周二到周六早上 `07:00`，对应美股周一到周五收盘后的数据。日志写入：

```text
storage/logs/daily-YYYYMMDD.log
```

如果要自定义端口、网页登录用户名、密码或定时时间，可以在运行前指定环境变量：

```bash
QQQ_ADVISOR_PORT=8080 \
QQQ_ADVISOR_USERNAME=advisor \
QQQ_ADVISOR_PASSWORD=换成一个很长的随机密码 \
QQQ_DAILY_CRON="30 8 * * 2-6" \
./deploy.sh
```

脚本会打印网页登录用户名和密码。请保存好密码，`.env` 不会提交到 Git。

### 手动部署步骤

如果不想用一键脚本，也可以按下面步骤手动操作。

### 1. 准备数据目录

在本机或服务器项目目录执行：

```bash
./scripts/prepare_storage.sh
```

它会把当前 `config.json` 和 `data/*.json` 复制到 `storage/`。如果是刚 clone 的新仓库且还没有 `config.json`，会使用 `config.example.json` 初始化：

```text
storage/config.json
storage/data/
```

### 2. 配置网页登录密码

复制环境变量模板：

```bash
cp .env.example .env
```

编辑 `.env`，至少设置：

```bash
QQQ_ADVISOR_USERNAME=advisor
QQQ_ADVISOR_PASSWORD=换成一个很长的随机密码
QQQ_ADVISOR_PORT=8765
```

设置 `QQQ_ADVISOR_PASSWORD` 后，网页会启用 Basic Auth。线上不要空密码运行。

### 3. 启动

```bash
docker compose up -d --build
```

访问：

```text
http://你的服务器IP:8765
```

如果你前面接 Nginx/Caddy 和域名，容器仍然只需要暴露本机端口，HTTPS 交给反向代理处理。

### 4. 安装每日定时任务

中国时区建议周二到周六早上 `07:00` 运行，对应美股周一到周五收盘后的数据：

```bash
./scripts/install_cron_daily.sh
```

它会安装一条宿主机 cron：

```cron
0 7 * * 2-6 cd 当前项目目录 && ./scripts/run_daily_docker.sh # qqq-advisor-daily
```

每日任务会进入正在运行的 `qqq-advisor` 容器执行：

```bash
python3 qqq_agent.py daily --config /app/storage/config.json
```

运行日志保存在：

```text
storage/logs/daily-YYYYMMDD.log
```

如果想改执行时间，例如每天 08:30：

```bash
QQQ_DAILY_CRON="30 8 * * 2-6" ./scripts/install_cron_daily.sh
```

卸载定时任务：

```bash
./scripts/uninstall_cron_daily.sh
```

### 5. 备份

备份整个持久化目录：

```bash
./scripts/backup_storage.sh
```

默认会生成：

```text
backups/qqq-storage-YYYYMMDD-HHMMSS.tar.gz
```

建议再把这个压缩包同步到另一台机器或对象存储。只留在同一台云服务器上，不算真正备份。

### 6. 迁移到新服务器

在旧服务器打包：

```bash
./scripts/backup_storage.sh
```

把生成的 `qqq-storage-*.tar.gz` 传到新服务器，在新服务器项目目录执行：

```bash
mkdir -p storage
tar -xzf qqq-storage-*.tar.gz -C storage
cp .env.example .env
# 编辑 .env 里的网页登录密码和端口
docker compose up -d --build
```

只要 `storage/` 完整，历史报告、建议记录、手动订单、真实成交记录和模型参数都会跟着迁移。

网页端可以完成：

- 输入可投资总金额、当前纳指基金持仓、风险偏好、投资期限、最大可接受亏损
- 维护你已经持有的纳斯达克基金，并展示净值、市值和盈亏快照
- 保存输入到本地 `config.json`
- 实时获取 QQQ 行情和基金净值快照
- 自动拉取 VIX、美债10年收益率、DXY 和 QQQ 成交量等外部因子
- 展示今日动作、建议买入金额、入场评分、关键指标、QQQ/纳指代理曲线和支付宝候选基金
- 按支付宝基金 T+1 估算确认/成交净值日
- 自动生成待手动确认的支付宝买入/卖出交易单
- 将每日建议和真实成交分开保存，便于分别复盘模型和实际执行
- 参考本地宏观事件摘要，对入场评分做风险调整
- 自动记录每日建议到 `data/advice_log.json`
- 按 1、5、20 个交易日复盘建议是否正确，并给出模型优化提示

## 第一次使用

如果还没有配置文件，可以初始化：

```bash
python3 qqq_advisor.py init
```

也可以直接复制模板：

```bash
cp config.example.json config.json
```

然后编辑 `config.json`，或直接在网页端填写并点击“保存输入”。`config.json`、`data/`、`storage/` 和 `.env` 默认被 `.gitignore` 排除，避免把个人资产配置、历史记录和密码上传到 GitHub。

配置示例：

```json
{
  "profile": {
    "total_investable_cny": 100000,
    "risk_level": "balanced",
    "horizon_years": 3,
    "max_acceptable_loss_pct": 20
  },
  "portfolio": {
    "current_nasdaq_position_cny": 0,
    "nasdaq_fund_holdings": [
      {
        "code": "270042",
        "name": "广发纳斯达克100ETF联接人民币(QDII)A",
        "amount_cny": 10000,
        "shares": 0,
        "cost_nav": 0
      }
    ]
  },
  "rules": {
    "min_trade_cny": 10,
    "alipay_only": true
  }
}
```

风险偏好：

- `conservative`：保守
- `balanced`：稳健
- `aggressive`：进取

持仓字段说明：

- `code`：基金代码
- `name`：基金名称
- `amount_cny`：你当前记录的持有金额
- `shares`：持有份额；如果填了份额和当前净值，系统会估算当前市值
- `cost_nav`：持仓成本净值；如果填了份额和成本净值，系统会估算盈亏

## 命令行使用

生成每日建议：

```bash
python3 qqq_advisor.py run
```

复盘历史建议：

```bash
python3 qqq_advisor.py evaluate
```

运行自动进化 agent：

```bash
./run_agent_daily.sh
```

用最近 100 个可标注交易日训练：

```bash
python3 qqq_agent.py train --lookback 100
```

让 Qwen 参与提出候选因子：

```bash
python3 qqq_agent.py train --lookback 100 --use-qwen-factors
```

当前外部因子会进入训练样本和网页报告，但新因子默认作为候选因子处理。只有本地回测验证优于当前参数后，Agent 才会把它们启用到 `data/model_params.json`。

只看训练结果、不应用新参数：

```bash
python3 qqq_agent.py train --lookback 100 --no-apply
```

只使用本地规则、不调用 Qwen：

```bash
QQQ_AGENT_NO_QWEN=1 ./run_agent_daily.sh
```

输出报告到指定文件：

```bash
python3 qqq_advisor.py run --output data/manual-report.json
```

如果你的 Python 证书链配置正常，可以开启严格 HTTPS 校验：

```bash
QQQ_ADVISOR_STRICT_SSL=1 python3 qqq_advisor.py run
```

## 每日记录建议

每次运行以下任一操作，系统都会记录当天建议：

```bash
python3 qqq_advisor.py run
```

或运行 agent 日常任务：

```bash
./run_agent_daily.sh
```

或在网页端点击：

```text
获取今日建议
```

记录位置：

```text
data/advice_log.json
```

同一个 QQQ 交易日只会保留一条记录。当天重复点击会更新同一条，不会产生重复记录。

## Agent 自动进化

Agent 入口：

```bash
python3 qqq_agent.py daily
```

它会完成：

1. 生成今日建议。
2. 写入 `data/advice_log.json`。
3. 复盘历史建议。
4. 生成候选模型参数。
5. 如果候选参数在本地回测中更好，则更新 `data/model_params.json`。
6. 把进化过程写入 `data/evolution_log.json` 和 `data/agent_state.json`。

Qwen API key 不要写入代码。使用环境变量：

```bash
export QWEN_API_KEY="你的新key"
export QWEN_MODEL="qwen3.6-plus-2026-04-02"
```

完整 Agent 原理见：

[AGENT.md](/Users/dz0401012/Desktop/pythonProject/qqq/AGENT.md)

## 宏观事件配置

宏观事件保存在：

```text
data/macro_context.json
```

如果文件不存在，程序会自动创建空模板。你可以手动写入国际局势、政治、经济或重大事件摘要：

```json
{
  "updated_at": "2026-05-10",
  "source": "manual_local_json",
  "currency": {
    "usdcny": 7.2,
    "usdcny_change_pct_30d": 1.5,
    "cny_direction": "depreciating"
  },
  "central_banks": {
    "fed": "neutral",
    "ecb": "neutral",
    "pboc": "neutral",
    "boj": "neutral",
    "global_bias": "neutral"
  },
  "usd_liquidity": "neutral",
  "events": [
    {
      "category": "geopolitics",
      "direction": "risk_off",
      "severity": 4,
      "summary": "重大地缘冲突升级，市场风险偏好下降",
      "expires_at": "2026-05-17"
    }
  ]
}
```

传给大模型时会自动压缩：最多 5 条事件、每条短摘要、只传参数变化和少量复盘样本。

字段说明：

- `usdcny_change_pct_30d`：美元兑人民币 30 日变化；正数通常代表人民币贬值。
- `global_bias`：全球央行方向，支持 `hiking`、`cutting`、`neutral`。
- `usd_liquidity`：美元流动性，支持 `tightening`、`easing`、`neutral`。

## 建议和真实投资分离

模型每天生成的是“建议”，保存在：

```text
data/advice_log.json
```

待你手动确认的支付宝交易单保存在：

```text
data/manual_orders.json
```

你在支付宝真实成交后，可以通过 API 或后续页面录入到：

```text
data/actual_trades.json
```

这样可以分别评估：

- 模型每日建议准不准。
- 你实际买卖执行效果如何。
- 买入和卖出的时间点是否合理。

## 每日定时运行

如果是 Docker Compose 云服务器部署，优先使用上面的 `./scripts/install_cron_daily.sh`。

裸机运行时，美股收盘后数据更完整。中国时区建议每天早上 `07:00` 以后运行：

```bash
python3 /Users/dz0401012/Desktop/pythonProject/qqq/qqq_advisor.py run --config /Users/dz0401012/Desktop/pythonProject/qqq/config.json
```

如果使用 `cron`，可以参考：

```cron
0 7 * * 2-6 cd /Users/dz0401012/Desktop/pythonProject/qqq && /usr/bin/python3 qqq_advisor.py run
```

说明：周二到周六早上运行，对应美股周一到周五收盘后的数据。

## 当前内置候选基金

- `270042` 广发纳斯达克100ETF联接人民币(QDII)A
- `006479` 广发纳斯达克100ETF联接人民币(QDII)C
- `161130` 易方达纳斯达克100ETF联接(QDII-LOF)A
- `012870` 易方达纳斯达克100ETF联接(QDII-LOF)C
- `019547` 招商纳斯达克100ETF联接(QDII)A

支付宝是否可买、是否限购、申购费、赎回费和到账时间会变，程序只做候选池和风控建议。

## 模型规则

完整模型说明见：

[MODEL.md](/Users/dz0401012/Desktop/pythonProject/qqq/MODEL.md)

简要逻辑：

1. 获取 QQQ 最新日线。
2. 计算 MA20、MA60、MA200、RSI14、近一年高点回撤、20日波动率。
3. 从 50 分开始，根据 `data/model_params.json` 里的因子和权重加减分。
4. 将评分映射成 `暂不买入`、`小额试探`、`正常买入`、`加仓`。
5. 根据你的总资产、风险偏好、投资期限和当前持仓，计算目标纳指仓位和本次建议买入金额。
6. 记录建议，未来用 1、5、20 个交易日后的 QQQ 表现复盘。

因子支持增删、启停和权重变化：

```text
data/model_params.json -> factor_model.factors
```

Agent 会探索因子权重的小幅变化，并通过本地复盘决定是否应用。

## 注意事项

- 本项目不会自动提交支付宝基金申购或赎回。
- 自动生成的交易单只用于提醒和记录，实际下单必须由你在支付宝里手动确认。
- QDII 基金确认和到账较慢，不适合短线频繁交易。
- 持有少于 7 天赎回通常成本很高。
- 程序展示的基金净值快照来自公开接口，交易前以支付宝为准。
- 当前复盘使用 QQQ 表现，不等同于人民币基金实际收益。
- 样本不足时不要急着修改模型参数。
