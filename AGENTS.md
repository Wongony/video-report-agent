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

## Report Skill maintenance

- When changing the report Skill, review the entrypoint, selected mode and affected references together. Keep shared rules in the entrypoint, mode differences in modes, and only distinct editorial guidance in references; preserve progressive disclosure.
- Before adding a rule, check whether an existing rule or template already covers it. Merge or replace that text first; remove superseded or duplicate rules in the same change. New behavior may justify growth, but a one-report workaround must not silently become a universal restriction.
- Report what was added, merged or removed, and the before/after Markdown character totals. Preserve factual boundaries, mode contracts and adopted design choices; compare representative frozen-input reports when behavior changes, using the existing evaluation tools and proportional validation.
- Weekly reviews propose specific deletions or replacements with reasons. They do not rewrite the Skill unattended, run paid generations, or adopt pending design suggestions; actionable findings require user selection. Keep review records under `.local/evals/`.

## Documentation Maintenance

本项目通过以下文档保存现状、后续方向和重要决策。执行项目任务或形成值得保留的项目讨论结论时，按实际影响维护相关内容，无需重新调用文档 Skill。

### 文档职责

- `docs/PROJECT.md`：项目解决的问题、目标与范围、当前能力、重要限制和文档入口。
- `docs/ARCHITECTURE.md`：当前系统结构、模块职责、关键流程、数据关系及重要接口。
- `docs/PLAN.md`：讨论过的候选方向、已确认的下一步和实际阻塞。
- `docs/DECISIONS.md`：影响后续开发的重要选择、决定性原因、约束、代价及替代关系。

PROJECT 和 ARCHITECTURE 描述当前状态；已确认但尚未实现的设计标注待实现。具体配置与流程在 ARCHITECTURE 或对应专题文档中完整维护，其他文档只保留必要摘要与链接。DECISIONS 保存选择理由，PLAN 保存未来事项，不重复抄写整套方案。

已有源码课程、面试材料或历史记录可以保留原用途；承担当前说明职责的部分应与现状一致，不能靠一条“以源码为准”保留错误。跨仓文档各自维护职责与接口，公共能力在所属仓库完整说明，产品集成在负责整体产品的仓库说明并引用，不向公开仓库复制私有内容。

### 开发前

较大功能、架构调整或跨模块修改前，阅读相关文档并结合代码理解实现；小范围修改按需查看。发现不一致时，区分文档过时、实现缺陷和方案变化，依据当前已确认方案处理；无法判断的重要约定保留并提出问题，不阻塞其他可继续工作。

### 文档更新

目标、能力、架构、数据、接口、重要依赖或策略发生变化，以及讨论形成有保留价值的方向时，更新对应文档。普通修复、样式或参数调整未使文档失真时，无需更新。

优先修改原说明，合并重复内容、删除失效说明；按变化检查关联的摘要、图表、示例和问答，不只在末尾追加日期补丁，也不扩大为全仓文档审计。README、部署和专题文档在原处维护，核心文档按需引用。

保持事实与状态准确，区分已实现、已验证和已部署。本轮未检查某项能力，不等于项目尚未实现或未投入使用，不自动产生待办。用户明确提供的使用情况应纳入相应说明；推断仍标明为推断。日期、提交基线和“不确定”说明仅在与具体内容有关时保留，不让某次调查范围长期代表整篇现状。

影响本次判断的价格、额度、地区可用性等外部信息，更新时核实官方来源并注明必要的适用条件；无需为无关的日常改动重查所有外部资料。

### PLAN 的维护

保存用户提出、讨论形成或用户要求保留的有价值方向。用简短文字说明想解决什么问题及关键背景，并区分候选、已确认、进行中或阻塞；仅在有依据时写优先级、时间和下一步。需要用户选择的关键问题放在相应事项下，不自行批准候选，也不为每个想法强制补齐实施方案。

相关讨论或任务使方向、状态或阻塞变化时，更新原事项。从 PLAN 移除已完成且没有后续工作的事项；失效或放弃的方向移除或简短说明原因。尚待部署或验证时，只保留真实剩余工作和相关说明链接。

不收录实现流水账、测试数量、提交记录或“本轮未检查”清单。长期有用的实现与验收说明留在已有对应文档或记录中，PLAN 按需引用。

### DECISIONS 的维护

只记录未来需要理解“为什么这样选择”的重要决定。简短写清日期、问题、选择、决定性原因及重要约束或代价。地区、预算、维护能力等确实影响决定时，保留相应背景；只有实际比较过且有长期解释价值时，才简述未选方案，不为补文档编造备选或理由。

旧决定被替代时，在旧条目上标明替代范围并链接新决定，保留原理由；仍有效的部分继续有效。提交、验收和部署进度在对应说明中维护，不作为每条决策的固定附录。普通修改与临时实验无需记录。

### 任务结束

只更新此次变化涉及的文档，并清理相关旧说明，不要求每次修改全部文件。不记录开发流水账，不为完整而增加低价值内容；没有影响时无需改文档。

文档入口为 `docs/README.md`。本仓库完整维护公共能力及其接口；产品集成与运营方向由 Service 维护，不向本仓库复制私有资料。
