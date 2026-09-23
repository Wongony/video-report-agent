# Video Report Agent — Public Core

This is the public ASR / transcript / Pi / shared Skill / renderer / CLI package.
Hosted accounts, credits, web routes, administration and deployment belong to the private service repository. Do not copy private source or merge private history here.

Keep a single sequential Python → Pi RPC → Skill generation path. Pi owns the agent loop. Each generation writes only to its run workspace. Use the smallest sufficient change; no speculative orchestration or unrelated refactors.

Use project-local .venv and uv. Run relevant tests. Do not claim fixture checks prove live model quality. Do not commit or push without user authorization.

Keep maintained docs and examples in docs/. Local implementation notes belong in .local/docs/ (ignored).

Evaluation tooling and approved public smoke/benchmarks belong in Core. Raw real-source inputs and experimental results belong in Core `.local/evals/` (ignored), not Service. Maintained docs stay with their owning repository and are excluded from deployment.

## Generation and verification

- Preserve Pi completion semantics: `agent_end` alone is not completion; the consumer waits for `agent_settled` and checks the final assistant `stopReason == "stop"`.
- Keep pipeline stage events in `run.trace.jsonl` and Agent details in `pi.events.jsonl`. Extend the relevant layer without unifying logs or introducing a tracing framework as incidental cleanup.
- For report quality comparisons, hold transcript and generation settings constant except for the variable under test. Assess source meaning (numbers, conditions, attribution and causality) separately from HTML validity and visual layout; valid source IDs alone do not establish fidelity.
- For report template/layout changes, inspect a representative rendered example at the affected viewport when feasible. State when only static/tests were checked, and whether the change affects future generation or an existing report. Do not regenerate old reports implicitly.

## Report design iteration

- For local component redesigns, read the selected mode's current template and `docs/report-design-preferences.md` first. Prototype inside that template with representative existing components; preserve the paper, canvas, typography and surrounding visual language unless the user requests a page redesign.
- When the user reviews alternatives and unambiguously selects one (for example, “D”), treat the selection as authorization to adopt it in the relevant generation Skill/template and record the preference. Do not merely switch the preview, ask for confirmation again, or wait for the word “adopt”. If the user explicitly asks only to preview or retain a sample, honor that scope. Ask only when the selected alternative or target mode is genuinely unresolved.
- Adoption applies to future generation in the selected mode. Commits, pushes, deployments and regeneration of historical reports still require their own user authorization.

## Documentation Maintenance

本项目维护以下文档，让开发者或 AI Agent 在没有历史聊天记录时，仍能理解项目当前状态、设计原因和下一步工作。

### 文档职责

- `docs/PROJECT.md`：项目目标、范围、当前能力、已知限制和文档入口。
- `docs/ARCHITECTURE.md`：系统结构、模块职责、关键流程、数据关系和重要接口。
- `docs/PLAN.md`：当前任务、待解决问题和明确的下一步。
- `docs/DECISIONS.md`：影响后续开发的重要设计选择、原因和代价。

PROJECT 和 ARCHITECTURE 以当前状态为主。已确认但尚未实现的设计明确标注状态；具体待办统一放在 PLAN 中，避免重复维护。

### 开发前

较大功能开发、架构调整或跨模块修改前，先阅读相关文档，并结合代码理解当前实现。小范围修改按需查看。

发现代码与文档不一致时，判断是文档过时、实现缺陷还是需求或设计变化，再按当前已确认方案修正。实现已经成为新的确定方案时，同步更新文档；不要把临时代码或未经确认的行为写成设计约定。无法判断的重要约定先保留并提出问题，不影响其他可继续的工作。

### 文档更新

当变化使现有说明失真，或影响后续理解、使用和开发时，更新相关文档，包括：

- 项目目标、功能范围、使用方式或限制变化；
- 架构、模块职责、数据流程、存储结构或接口变化；
- 重要依赖、模型、外部服务、配置或部署方式变化；
- 重要的性能、成本、可靠性、安全策略或设计取舍变化；
- 当前计划、优先级或阻塞情况变化。

普通 bug 修复、样式调整、小范围重构和参数修改，若不影响上述内容且文档仍准确，可以不更新。

README、部署文档、接口文档等已有说明直接在原处维护，核心文档按需引用，不重复抄写。

### 任务结束

结束任务时，按实际影响更新相关文档，不要求每次修改所有文件：

- 保持当前状态准确，区分已实现、已验证和已部署；
- 清理 PLAN 中已完成或失效的事项，将有长期价值的结论留在对应文档；
- 任务未完成但进展或阻塞发生变化时，在 PLAN 中留下当前状态和下一步。

不记录开发流水账，不为了完整而增加低价值内容。

### 重要决策

DECISIONS 只记录未来需要理解“为什么这样设计”的重要选择。每条用简短文字写清日期、问题、决定和原因；有重要限制或代价时一并说明，不强制固定表格。

普通代码修改、临时实验无需记录。旧决定被替代时，保留原来的原因并标明新的生效方案。

文档入口为 `docs/README.md`。历史决策以本仓库 Git、已有文档与当前源码为依据；明确区分原始理由、工程分析和待确认事项。跨仓库变化分别维护所属文档，不向公开 Core 复制 Service 私有实现或运营资料。
