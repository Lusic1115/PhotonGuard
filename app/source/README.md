# PhotonGuard 开发与源码运行

`0.2.0rc28` · AI 光学学习与实验工作台。完整产品介绍和 Windows 启动方式见[项目首页](../../README.md)。

## 在 Linux / Ubuntu WSL 中运行

保留源码目录结构，使用 Python 3.12 环境。安装快速计算和数据分析依赖后，在本目录执行：

```sh
python -m pip install -r release/requirements-fast.txt -r release/requirements-analysis.txt
PHOTONGUARD_PYTHON="$(command -v python)" bash START_PORTABLE.sh --data my-experiments --port 8766
```

浏览器打开 `http://127.0.0.1:8766/experiments`，知识库入口为 `/knowledge`。已有锁定运行环境时，也可以使用 `runtime/bootstrap.sh` 准备完整工具环境。启动和本地浏览不会请求模型。

需要 AI 时在页面连接个人 DeepSeek API Key，并设置累计请求上限。默认 20 次，可自行提高；新实验求解经用户核对计划并批准后执行。账户连接、费用和数据说明见[使用指南](../../docs/使用指南.md)。

## 主要目录

| 目录 | 内容 |
|---|---|
| `photonguard/experiments/` | 条件、计划、执行、版本、知识检索与光学助教 |
| `photonguard/workflow_assets/` | 页面、样式、交互与本地公式显示 |
| `photonguard/case_library/` | 可执行的光学案例 |
| `photonguard/experiments/knowledge_data/` | 知识正文、专题和来源 |
| `examples/inverse/` | 双缝、高斯与膜厚的离线复用示例 |
| `tests/` | 软件行为与数值合同测试 |
| `validation/` | 本地 HTTP 流程检查与测试工具 |
| `runtime/` | 可选完整环境及锁定依赖 |

## 数据与模型

- 薄膜正向计算使用 tmm；膜厚分析使用已知折射率、法向入射、无损单层和校准反射率。
- 衍射使用理想薄透镜焦平面模型。第一暗纹等指标由登记几何公式核算，二维光场由实际数值工具计算。
- 高斯传播使用理想基模与 q 参数／ABCD；半径采用 1/e² 强度半径。
- 双缝数据可使用 `position_mm,intensity_relative`；高斯可使用 `z_mm,radius_1e2_um`；膜厚使用 `wavelength_nm,reflectance_calibrated`。导入时核对单位、固定条件及数据来源。

薄膜、衍射与高斯任务各自保存条件、计划和结果。教学合成数据标明来源，用户观测保留原始输入。

## 导出与复跑

工作台可导出只读 HTML、CSV、数组和完整任务 ZIP。解压导出的任务后，按保存计划运行：

```sh
python -m photonguard.experiments.cli run /path/to/export/execution/plan.json --out rerun-experiment --approve
python -m photonguard.experiments.inference_cli /path/to/export/fit-execution/request.json --out rerun-fit --approve
python -m photonguard.experiments.inverse_cli /path/to/export/inverse-execution/request.json --out rerun-inverse --approve
```

选择对应任务类型的命令，并使用新的输出目录。保存计划复跑不需要模型 Key；依赖和运行条件随计划核对。三个直接调用示例见[离线反向分析](examples/inverse/README.md)。

## 知识包扩展

知识条目记录正文、来源、适用条件、公式符号、权利状态和关键结论。可独立检索，也可与学习问答、当前实验结果关联。格式与安装方式见[知识条目结构](../../knowledge-schema.md)和[知识包说明](../../docs/知识包说明.md)。

## 测试与贡献

在单独开发环境安装 pytest 后运行需要的测试：

```sh
python -m pytest tests/test_film_stack_intake.py
```

测试代码中的合成回复用于复核接收和处理路径；需要在线模型的操作使用个人账户和明确设置。请提交最小复现步骤，避免在 Issue、日志或代码中包含 Key 和私人数据。

项目采用 GPL-2.0-or-later。第三方依赖保留各自许可，见 [THIRD_PARTY](docs/THIRD_PARTY.md)。AI 辅助参与实现、调试、测试和文档整理，见[开发说明](docs/AI_ASSISTANCE_ZH.md)。
