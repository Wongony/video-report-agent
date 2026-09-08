# ASR backend implementation validation — 2026-09-09

## Evidence

- Relevant regression tests: 69 passed (ASR, provider protocol/configuration/redaction, OCR/Foundation, reuse, Web).
- Comparison isolation/offset test: 1 passed. This test uses mocked recognition and real Canonical construction.
- Clean temporary project `.venv`, without either `mlx` or `mlx_whisper`: pipeline and Paraformer imports passed; 13 protocol/configuration tests passed. These are mocked cloud responses, not live Paraformer evidence. The isolated project was `/tmp/video-report-cloud-check`.
- MLX live fixed clips: all nine completed and generated ASR-only Canonical. Outputs: `runs/asr-compare-d4dc9f71bd0242c29146cedd05ac47e4/`.
- MLX live full pelican video: 491.712 seconds of audio; transcription took 23.303 seconds (RTF 0.0474); 295 ASR segments. Normal Transcript Foundation entry point generated Canonical. Outputs: `runs/asr-compare-7817cd6f9e234fecaf2bea5963a8bdae/`.
- An earlier sandboxed MLX attempt aborted in the native runtime. Its incomplete diagnostic directory is `runs/asr-compare-2b12771dee2a46bd98b75f034baaa20f/`; it is not acceptance evidence. Both completed MLX runs above used Apple Metal access outside that restriction.

## Incomplete acceptance and observed limitations

- `DASHSCOPE_API_KEY` was absent. Every Paraformer sample is NOT_RUN and both comparisons are INCOMPLETE; no cloud request or cloud accuracy/cost claim was made.
- Actual cloud upload, billing, and live timestamp semantics remain to be verified with a Beijing credential. The clean-environment check proves dependency separation and mocked protocol execution only, not a live cloud transcription or a Linux deployment.
- Human listening is pending. No CER, terminology accuracy, or superiority conclusion is reported. Term occurrences are only review aids.
- The full-video MLX last segment ends at 492.320 seconds, 608 ms after the extracted audio duration. The original timestamp is preserved for review; successful Canonical construction does not prove timing accuracy.
- Full-video text contains zero exact occurrences of “鹈鹕” and eight of “SVG”. This is a review cue, not a correctness judgment without listening.

## Repeat

Set the Beijing `DASHSCOPE_API_KEY` locally, then run:

```bash
uv run --extra mlx video-report compare-asr --manifest evals/asr/fixed-clips.json
uv run --extra mlx video-report compare-asr --manifest evals/asr/full-video.json
```

Each command creates fresh independent outputs. Both backends receive the same audio;
Canonical uses ASR-only/OCR-off and no Pi model is invoked. Source offsets are recorded
separately and added only in `source-mapping.json`. The existing manifests reference
local downloaded media; if media retention removes those files, restore the same source
files or update their locations before repeating.
