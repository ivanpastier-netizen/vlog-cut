# Model routing: local volume, strong-model judgment

Use the cheapest component that can do the task reliably. This is an authority boundary, not
a preference: deterministic scripts own facts, local models suggest observations, and a strong
model owns editorial judgment.

| Work | Default | Authority |
| --- | --- | --- |
| Probe, duration, timestamps, proxy/render/QC state, checksums | Local scripts | Source of truth |
| Transcription | Local `mlx-whisper` | Draft evidence; speech must be coherent before quoting |
| High-volume frame pre-triage | Local Ollama vision model (`triage_model`) | Advisory candidates only |
| Story, pacing, continuity, setup/payoff, humour, emotion | Strong model | Editorial decision |
| Ambiguous action, hero/final selection | Strong model with visual verification | Editorial decision |
| Render acceptance and delivery | Scripts plus visual QC | Must pass the QC gate |

## Local pre-triage

Run after proxies exist, especially for more than about 20 clips:

```bash
python3 "$VLOG_SKILL/scripts/local_triage.py" <project-dir>
```

It samples frames at 20 %, 50 % and 80 % of each proxy, stores raw per-frame observations and
forms a consensus only when at least two frames agree. Results go to `local_triage.json`,
separate from `shot_index.json`. It resumes by proxy fingerprint and model/prompt version. Use
`--dry-run`, `--limit` or `--clip HHMMSS` to bound work; `--force` refreshes.

Local output may prioritise what the strong model inspects. It must never:

- set `keep: false`, delete, hide or permanently exclude footage;
- name or identify a person, infer a relationship or quote speech;
- decide emotion, intent, humour, narrative importance or a hero shot;
- author the EDL, approve QC, publish or archive a final;
- be presented as ground truth when frames disagree or the model is uncertain.

The strong model receives the compact index, transcript excerpts, local observations and
labelled candidate frames — not every raw frame. It verifies anything used in the story and
inspects all locally uncertain clips plus a sample of ordinary ones.

## Choosing a local model

Benchmark before adopting: run it on a day you have already labelled and compare. Small
vision models are useful noisy first-pass observers, not editors. Reasoning models whose chat
template spends the output budget on thinking often return no dependable structured output —
test for that explicitly. A model upgrade never expands local authority; the no-exclusion and
no-story rules stay in force.
