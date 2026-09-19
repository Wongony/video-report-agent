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
