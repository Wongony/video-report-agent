# Public smoke eval

These three fictional, project-authored transcripts exercise numbers, conditions and attribution. They are not real video transcripts, a holdout set, or evidence of model quality.

Offline structure and runner checks (no provider calls):

```sh
uv run pytest -q tests/test_evaluate_report.py
```

Optional live A/B generation from the core root (uses configured model credentials and incurs provider usage):

```sh
uv run python -m video_report_agent.evaluate_report --manifest evals/report-review/manifest.json
```

Review source fidelity manually; successful rendering alone does not validate meaning. Outputs go to ignored `runs/`.
