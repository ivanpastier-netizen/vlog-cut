---
name: vlog-cut
description: "Produce or revise personal/family vlogs locally on a Mac: ingest camera, card or Apple Photos footage, inspect and select shots, build a beat-aware EDL, render and QC a review copy, then publish the approved final. Use for 'cut my vlog', 'edit today's footage', 'I uploaded my videos', vlog feedback and re-cuts, short share cuts, and archiving a finished vlog."
metadata:
  version: "1.1.0"
---

# vlog-cut

Produce vlogs through the local scripts in this skill. Prefer deterministic inspection and
rendering over asking the model to remember project state. Set the skill root once:

```bash
VLOG_SKILL="${VLOG_SKILL:-$HOME/.claude/skills/vlog-cut}"
```

Paths (ingest root, music library, review folder, optional archive host) come from
`~/.config/vlog-cut/config.json` or `VLOG_*` environment variables — see
`config.example.json`, and `python3 "$VLOG_SKILL/scripts/vlog_config.py"` prints the resolved
values. Never hard-code a user path into a script or EDL template.

All ordinary work is local: ffmpeg/VideoToolbox, local mlx-whisper, optional local Ollama,
the filesystem. The internet is not part of the editing pipeline.

## Start every run from measured state

```bash
python3 "$VLOG_SKILL/scripts/vlog_status.py" <project-dir>
```

Use its `next` result as the default next action. Do not reconstruct state from chat or
filenames when the script can inspect it. With no project directory it checks the local
toolchain and any `pending.json` review hand-off.

## Route to one playbook

Read only the reference needed for the current phase, then act:

- New footage, ingest, proxies, transcription, project state →
  [workflow.md](references/workflow.md)
- Shot selection, story, EDL, music, render, re-cut, QC →
  [editing-qc.md](references/editing-qc.md)
- Approved final, export ladder, delivery, archive →
  [delivery-archive.md](references/delivery-archive.md)
- HDR, mixed cameras, night footage, exposure/colour mismatch → additionally
  [color-hdr.md](references/color-hdr.md)
- Model choice, local Ollama triage, cost/quality routing →
  [model-routing.md](references/model-routing.md)

## Default behaviour

The user's normal input is "I uploaded today's photos and videos". Continue without asking
them to perform pipeline steps. Mechanical processing is automatic; creative selection still
requires looking at contact sheets and rendered frames.

1. Ingest and analyse locally.
2. Build one authored EDL and derive requested lengths from it.
3. Render a 720p SDR review.
4. Run the complete QC gate and inspect its visual sheets.
5. Copy the finished review to the configured `review_dir` and run `review_hook` if set.
6. Stop at `awaiting-review`. Publish only after the user approves.

## Safety and quality invariants

- Never delete or modify camera originals. A source deletion requires explicit approval and
  a verified second copy.
- Never publish a new cut before review approval.
- Never claim a cut is finished without inspecting the rendered review and a QC PASS.
  Contact sheets alone are insufficient.
- Address shots by capture-time key, not mutable clip index, on multi-wave or multi-day
  projects.
- Use different workdirs for the 720p review and the 4K master. Cached segments do not
  encode resolution or bitrate in their filename.
- A structural EDL change invalidates the indexed segment cache. A trim-only change needs
  the affected segments and any stale audio stems removed.
- Run long proxy, sheet, render and QC jobs in the foreground or a persistent session with
  logs; never let a short timeout kill them, and never end your turn while one is running.
- Treat detector uncertainty explicitly. A low-confidence mismatch is not a hard failure;
  every detector change needs a known-good and a deliberately broken test.
- Before finals, compare project mtimes/logs with `pending.json`; another session may already
  be rendering or distributing.
- Verify every archive transfer by checksum. Command success or file presence is not
  integrity.
- Footage of other people — especially children — is private by default. Never upload,
  share or publish anything unless the user explicitly asks for that specific file.

## Model-efficiency contract

Keep judgment narrow and mechanics scripted, so the skill also works on smaller models:

- Run `vlog_status.py` before reasoning about next steps.
- Use the provided scripts and EDL template; do not recreate ffmpeg pipelines from memory.
- Load one focused reference at a time.
- Make shot decisions from labelled images and transcripts, never from filenames.
- Local models do high-volume observable pre-triage only. Their output is advisory: it may
  add candidates but never exclude a shot, identify a person, decide the story or publish.
- A strong model owns story structure, humour, emotion, continuity, ambiguous scenes and
  final editorial judgment.
- State uncertainty instead of inventing scene content, speech, project state or archive
  availability.
- Escalate only genuinely creative choices to the user. Tool availability, artifact
  presence, counts, freshness and QC are machine-checkable.

## Completion report

Report only verified facts: project path, stage reached, review/final file paths, QC result,
whether review approval is pending, and any unavailable dependency or destination. Do not
describe an intended render or transfer as completed.
