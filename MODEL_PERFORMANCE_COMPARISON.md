# Model Performance Comparison — qwen-laptop (i3-6100U, 3.8 GB RAM)

Measured locally on the laptop via Ollama 0.40.4 (`audit_evidence/bench_*.json`, `scripts/bench.sh`). Prompt for short case: "Return exactly the word READY followed by the number 42." (output cap 16); medium: 3-sentence summary (cap ~96). **These are direct local-Ollama numbers, not distributed-route numbers (Tests D/E are BLOCKED).**

| Metric | qwen2.5:1.5b | llama3.2:1b |
|---|---|---|
| Cold load (caches dropped), baseline | 38.5 s wall (load 33.4 s) | 28.6 s (load 27.3 s) |
| Cold load, quick A/B runs | 19.1–19.6 s | 24.4–25.2 s |
| Warm short latency | 0.37–0.41 s | 0.37–0.38 s |
| Warm short tok/s | 17.0–17.3 | 14.3–14.7 |
| Warm medium tok/s (latency) | 13.1–13.4 (9.0–14.4 s, 86 tok) | 10.7 (11.5 s, 96 tok) |
| Time to first token, warm | 0.08–0.11 s | 0.10 s |
| Peak Ollama RSS | 1.11–1.17 GB | 1.50 GB |
| Min MemAvailable | 1.02–1.09 GB | 0.69–0.71 GB |
| Avg CPU during gen | ~50–54 % | ~50–54 % |
| Peak temp | 45–54 °C | 47–55 °C |
| Failures | 0 | 0 |
| Answer correctness ("READY 42") | yes | yes |

Qwen is ~20–25 % faster in generation and uses ~25 % less RAM than Llama here. Both fit in RAM individually; loading both at once is avoided (`OLLAMA_MAX_LOADED_MODELS=1`).

## Swap
36 GB swap is essentially **unused** once a model is warm (swap-in/out ≈ 0). The only swap traffic was the baseline cold load after `drop_caches` (≈100k pages out for Qwen). It is a harmless safety net, not capacity: 36 GB swap ≠ 36 GB RAM, and on this ~90 MB/s disk a model that genuinely paged would be unusably slow. Swap was **not** disabled or shrunk (no demonstrated harm).

## Threads (qwen, medium prompt)
`num_thread` 2 → 13.45 tok/s, 3 → 13.07, 4 → 12.86. Default (2 physical cores) is best; not changed.

## Tuning A/B (quick mode)
| Config | Qwen cold | Llama cold | Warm tok/s |
|---|---|---|---|
| baseline (swappiness 15, powersave) | 38.5 s* | 28.6 s* | 17.3 / 14.3 |
| swappiness 5 | 19.6 s | 25.2 s | 17.3 / 14.5 |
| swappiness 5 + `performance` governor | 19.1 s | 24.4 s | 17.3 / 14.6 |
\* baseline cold included `drop_caches`; quick runs did not, so cold numbers are **not** a clean comparison. Warm throughput is unchanged (<1 %) in all configs.

**Decision:** governor change and swappiness change rejected (no demonstrated warm-path benefit; cold difference confounded). Swappiness 15 / `powersave` restored and verified. No larger models downloaded.
