# jev-triage-cn

一个用于中文短文本分流和置信度校准演示的小工具。它将 TypeSafe Jev 的 Choice 输出、缓存和本地 Wilson 校准报告组合起来，方便实验“准确率目标 → 自动处理比例”的关系。

> 非官方个人项目，与 TypeSafe 无关联；不使用其名称或标志暗示官方出品。

## 使用前

需要你自己的 `TYPESAFE_API_KEY`，所有 API 费用由使用者承担。实测的合成数据用量仅作预算参考：电商 120 条约 `$0.0034`，短信 60 条约 `$0.0016`；价格以 TypeSafe 官方当前价格为准。

密钥只能经环境变量传入。请在终端交互式设置并导出该变量；不要把密钥写进命令历史、配置文件、CSV 或任何仓库文件。

默认 `--backend` 是 `mock`，它只产生离线假数据。真实调用必须显式写 `--backend jev`。输入文本会发送到 TypeSafe 的 API，请先查看其数据政策。

## 快速开始

需要 Python 3.10+。在项目目录中安装：

```bash
pip install -e .
```

安装本项目后，按由小到大的顺序运行：

```bash
# 先离线估算；不会联网
jevtriage run --config configs/ecommerce.yaml --input data/ecommerce_labeled.csv --backend jev --dry-run

# 再用分层小样本验证；会显示预计请求数并要求确认
jevtriage report --config configs/ecommerce.yaml --labeled data/ecommerce_labeled.csv --backend jev --sample 10 --seed 42

# 最后才运行全量校准
jevtriage report --config configs/ecommerce.yaml --labeled data/ecommerce_labeled.csv --backend jev --target 0.90
```

`--sample N --seed S` 会按标签分层抽样，每类最多带一条故意模糊样本。真实结果缓存于本地 `.cache/`；同一文本、配置、后端和请求模型版本再次运行时不会重新请求。

## 配置与报告

YAML 配置定义分类 Choice、选项边界、附加 yes/no Choice 和 `review_band`。结果包含完整 probabilities、最大概率和 confidence。报告把带标签数据按种子 1:1 拆分，拟合集选择满足 Wilson 95% 下界目标的最低阈值，验证集只作独立检查。

报告会比较 `confidence` 与 `top_probability`，并输出三档分流、置信度校准表及供人工检查的分歧 CSV。未满足样本量或 Wilson 条件时，报告会明确说明原因，而不是硬凑阈值。

## 分歧样本

以下为真实 Jev 运行在合成数据上的 7 条分歧；仅作人工复核提示，**没有修改任何标签**。

复核标记为作者的初步主观判断，未经独立标注。

| 场景 | ID | 复核标记 | 标签 → Jev 选择 |
|---|---|---|---|
| 电商 | `ec-other-01` | 标签存疑 | other → positive |
| 电商 | `ec-other-03` | 轻微误读 | other → inquiry |
| 电商 | `ec-other-15` | 模型误读 | other → positive |
| 电商 | `ec-positive-21` | 标签存疑 | positive → negative |
| 短信 | `sms-fraud_suspected-12` | 模型误读 | fraud_suspected → normal |
| 短信 | `sms-marketing-05` | 标签存疑 | marketing → normal |
| 短信 | `sms-fraud_suspected-07` | 模型误读 | fraud_suspected → verification_code |

## 已知局限

- 数据均为合成；标签由 AI 按与提示词相同的规则编写。
- 合成数据中电商为 116/120、短信为 57/60；数据可能偏简单，不能代表真实业务。
- “是否需要 24 小时内人工处理”这一附加问题尚未评估。
- 短信场景仅供实验，曾把“索要验证码”判成“验证码通知”（`sms-fraud_suspected-07`）；它不能作为防诈骗工具。
- 数据中的验证码、机构名均为虚构。

## 安全

- 不要把 API key 提交进仓库。
- 输入数据请先脱敏。
- 工具不把请求正文保存到日志；缓存仅在本地 `.cache/`。

## English summary

`jev-triage-cn` is an unofficial personal experiment for Chinese text triage and local confidence calibration with TypeSafe Jev. Use your own API key and pay your own API costs. The included data and reports are synthetic demonstrations only; they are not evidence of real-world accuracy or fraud-detection capability.
