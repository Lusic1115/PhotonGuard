# PhotonGuard CI 测试策略

## 目标

持续集成用于验证仓库中的离线软件行为、数值合同、工作流状态、安全边界和数据完整性。CI 结果不等同于真实仪器验证、真实 Meep/FDTD 计算或在线 AI 模型效果验证。

## 环境

- Ubuntu GitHub-hosted runner。
- Python 3.12；项目声明支持 Python >=3.10。
- 安装 \`release/requirements-fast.txt\`、\`release/requirements-analysis.txt\` 和 pytest。
- 以 editable 模式安装项目，验证包元数据和导入路径。

## 网络与费用保护

- CI 将 \`DEEPSEEK_API_KEY\` 显式设为空，不向工作流注入服务商密钥。
- 设置 \`PHOTONGUARD_CI_OFFLINE=1\`，启用 pytest 插件中的 Python socket 层出站拦截。
- \`127.0.0.0/8\`、\`::1\` 和 localhost 可用于本地 HTTP 服务测试；Unix-domain socket 可用于本地 IPC。
- DeepSeek 后端测试必须使用 mock transport / 合成响应，不得发起真实模型请求。
- 此拦截是 pytest 进程内的 Python socket 保护，不是操作系统级防火墙；子进程或其他非 Python-socket 网络机制不在该插件的保证范围内。
- 依赖安装发生在 pytest 插件启动之前，需要访问包索引；该阶段不配置任何 AI 服务商凭证。
- 若新增外部服务测试，必须使用 mock 或另设人工触发且独立授权的流程；不得加入普通离线 CI。

## 测试分层

| 层级 | 内容 | 普通 CI |
|---|---|---|
| 核心单元测试 | 公式、数值计算、输入校验、数据结构 | 运行 |
| 工作流与安全测试 | 任务规划、审批、版本管理、CSRF、路径和凭证保护 | 运行 |
| 本地集成测试 | loopback HTTP 服务、文件导入导出、工作进程协议 | 运行 |
| 可选依赖测试 | LightPipes 等可选包相关测试 | 依赖缺失时按测试自身的 \`importorskip\` 规则跳过 |
| 真实外部验证 | 在线 DeepSeek、真实仪器、实际 Meep/FDTD 求解 | 不运行 |

## 失败策略

- 测试收集错误或测试失败均使 CI 失败。
- 不允许通过忽略失败、强制标记成功或静默跳过必需测试来获得绿色状态。
- JUnit XML 报告作为 GitHub Actions artifact 保存。
- CI 报告中的通过/失败/跳过数量必须与实际 pytest 输出一致。

## 本地复现

在 \`app/source\` 目录中安装依赖后运行：

\`\`\`bash
python -m pip install -r release/requirements-fast.txt -r release/requirements-analysis.txt pytest==9.1.1
python -m pip install -e .
PHOTONGUARD_CI_OFFLINE=1 PYTHONPATH=".:tests" python -m pytest -p photonguard_ci_network_guard -q
\`\`\`

如需检查完整测试收集情况：

\`\`\`bash
PHOTONGUARD_CI_OFFLINE=1 PYTHONPATH=".:tests" python -m pytest -p photonguard_ci_network_guard --collect-only -q
\`\`\`
