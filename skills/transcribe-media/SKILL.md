---
name: transcribe-media
description: Transcribe an audio or video file to text, SRT subtitles, or structured JSON using a local Whisper model via the stt CLI. Use when the user gives you a path to a media file (mp3, mp4, m4a, wav, mov, mkv, ...) and asks for a transcript, captions, what-was-said, or anything that needs the spoken content. Also use as a fallback when other skills (e.g. youtube-video-summary, bilibili-video-summary) couldn't get subtitles. Runs offline; on Apple Silicon it uses mlx-whisper for ~30x realtime, otherwise faster-whisper on CPU. Auto-installs the stt project at $STT_HOME (default ~/repos/stt) on first call if missing — subsequent calls are fast.
timeout_seconds: 1800
---

# Transcribe Media

Local Whisper transcription wrapper. Hands a media file to the stt project's CLI and returns JSON / SRT / text.

## When to use

- User gives a file path (audio or video) and wants a transcript / subtitles / "what does this say"
- Another skill needs spoken content but couldn't get captions (e.g. a bilibili video without subtitle tracks)
- User needs offline transcription (no API key, no upload to cloud services)

## When NOT to use

- The source already has subtitles available via `yt-dlp` or platform APIs — fetch those instead, much cheaper
- The user wants real-time / streaming transcription — this skill is one-shot only
- The deliverable is a translation to a different language — chain transcription + a translation skill

## Invocation

```bash
./.venv/bin/python ${OMA_AGENT_HOME}/skills/transcribe-media/scripts/transcribe.py \
  --input '<absolute-path-to-media>' \
  --format json
```

The script forwards everything to the stt CLI and translates its output to the oh-my-agent convention (JSON envelope on stdout, exit code reflects success/failure).

## Arguments

- `--input PATH` (required) — path to the media file. `~` is expanded.
- `--format {json,srt,text}` (default `json`) — body shape inside the JSON envelope. `json` returns structured segments; `srt` returns a single SRT string; `text` returns plain joined text.
- `--language CODE` (default `auto`) — ISO 639-1 code (`zh`, `en`, ...) or `auto` to detect.
- `--engine {faster-whisper,mlx}` (optional) — backend override. Defaults to whatever stt's `set.ini` configures. On Apple Silicon, `mlx` is dramatically faster.
- `--model NAME` (default `large-v3-turbo`) — Whisper model. `large-v3-turbo` is the recommended default — best speed/quality balance.

## Output

**The wrapper always emits a JSON envelope on stdout** regardless of `--format`. (`--help` is the one exception; argparse handles it directly with plain text + exit 0.)

`--format json` (default):
```json
{
  "status": "ok",
  "engine": "mlx",
  "model": "large-v3-turbo",
  "language": "zh",
  "duration_seconds": 3970.2,
  "elapsed_seconds": 133.4,
  "segments": [{"start": 0.0, "end": 24.26, "text": "我们今天很开心..."}]
}
```

`--format srt`:
```json
{
  "status": "ok",
  "engine": "mlx",
  "model": "large-v3-turbo",
  "language": "zh",
  "duration_seconds": 3970.2,
  "elapsed_seconds": 133.4,
  "srt": "1\n00:00:00,000 --> 00:00:24,260\n我们今天很开心...\n\n2\n..."
}
```

`--format text`:
```json
{
  "status": "ok",
  "engine": "mlx",
  ...
  "text": "我们今天很开心...\nHello,大家好,我是..."
}
```

**On failure**, stdout is a JSON error envelope and exit code is non-zero:

```json
{"status": "error", "kind": "model_unavailable", "message": "..."}
```

`kind` values:
- `invalid_input` (exit 2) — file missing, ffmpeg not on PATH, bad arguments
- `ffmpeg_failed` (exit 2) — ffmpeg conversion error (e.g. corrupt media)
- `model_unavailable` (exit 1) — mlx-whisper not installed; HF model not found; download failure
- `transcribe_failed` (exit 3) — whisper backend raised mid-run
- `timeout` (exit 3) — stt CLI exceeded the wrapper's 1700s budget; process group terminated to clean up any in-flight ffmpeg child
- `stt_not_found` (exit 2) — `$STT_HOME` doesn't point to a valid stt install AND `STT_AUTO_INSTALL=0` (auto-install was opted out)
- `stt_install_failed` (exit 1 or 2) — auto-install attempted but a step (clone / venv / pip) failed; message has the relevant tail of the failing tool's stderr
- `internal` (exit 4) — programming error / unexpected exception (e.g. stt CLI emitted unparseable stdout)

Switch on `kind` for retry-vs-hard-fail. Network-related `model_unavailable` is retry-able; `invalid_input` is not. `timeout` is borderline — usually means a hung model or huge input.

## Performance hints

- 30s clip: ~2s end-to-end (model load dominates first call; subsequent calls reuse the cached model)
- 1 hour clip: ~2 min on Apple Silicon + mlx; ~30 min on CPU + faster-whisper + medium model
- The first call ever downloads the model (~1.6 GB for `large-v3-turbo`); subsequent calls hit the local cache

## Configuration

`$STT_HOME` (default `~/repos/stt`) tells the skill where to find — or where to install — the stt project. The skill auto-installs on first call if both `$STT_HOME/cli.py` and `$STT_HOME/venv/bin/python` are missing.

**First-call install steps** (logs to stderr, never stdout):
1. `git clone --depth 1 https://github.com/TCoherence/stt.git $STT_HOME`
2. `python3 -m venv $STT_HOME/venv`
3. `pip install -r requirements.txt` (torch + faster-whisper, ~1–3 min on cold pip cache)
4. On Apple Silicon (`Darwin/arm64`): `pip install mlx-whisper` + flip `engine=faster-whisper` → `engine=mlx` in `set.ini` for the ~30x realtime payoff. mlx install failure is non-fatal — falls back to faster-whisper CPU.

After install completes, the skill runs the requested transcription in the same call. So a fresh first call takes ~2–4 min total (install + first model download + transcribe); subsequent calls are fast.

**Opt out**: set `STT_AUTO_INSTALL=0` to disable auto-install. The skill then returns `kind: stt_not_found` with a setup hint instead.

**Pre-existing partial install**: the skill is idempotent — if `$STT_HOME` exists with a `.git/` but missing venv, only the venv + pip steps run. If `$STT_HOME` exists without `.git/` (a directory not recognized as our checkout), the skill errors with `kind: stt_install_failed` rather than risking destruction of user data.

**Manual setup (if you prefer)**:

```bash
git clone https://github.com/TCoherence/stt.git ~/repos/stt
cd ~/repos/stt
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# Apple Silicon — enable mlx backend
pip install mlx-whisper
sed -i '' 's/^engine=.*/engine=mlx/' set.ini
```

## Notes for skill authors

- Pass `subprocess.run([...], capture_output=True, text=True)` and parse `stdout` as JSON. No need to read stderr — successes have empty stderr (or just install progress lines on the very first call), errors mirror their JSON envelope to stdout.
- The first call after a fresh deploy may take a few minutes (auto-install). Don't add tight per-call timeouts client-side; the skill respects `timeout_seconds: 1800` from frontmatter and hard-caps the inner subprocess at 1700s.
- `--format json` returns segments with start/end timestamps in seconds (float). Useful for chaptering, jumping to time codes, or attribution heuristics.
- `--format srt` and `text` return the formatted body inside the JSON envelope under the `srt` / `text` key — extract with `data["srt"]` and `Path("out.srt").write_text(data["srt"])` for direct file write.
- For a 2-speaker interview/podcast, the LLM consuming this can usually infer who said what from context — speaker diarization is rarely worth the extra ~10x compute time.
- The wrapper uses `start_new_session=True` so a hung whisper inference cannot leave an orphan ffmpeg child past the agent's 1800s timeout.
