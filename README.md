# Hataktzir: AI that shortens long-form video into ready-to-publish clips

An end-to-end Python system that takes a long video (hours of talk), finds the moments worth watching, and turns each one into a finished clip with a title, description, thumbnail and creator credit, with no manual editing. The only human step is a one-tap approval.

It runs today on 6-12 hour livestreams of Israeli creators, published on [@hataktzir](https://www.youtube.com/@hataktzir). Nothing in the pipeline is specific to streams: any long recording where the value is in what people say (lectures, podcasts, interviews, workouts) goes through the same steps.

Site: https://hataktzir.github.io

## How it works

```
New long-form video detected (live/VOD pages: Kick, YouTube, Twitch via yt-dlp)
        │
        ▼
Download (yt-dlp, HLS; low-res audio for analysis, full res only for the chosen parts)
        │
        ▼
Transcription (faster-whisper on CUDA, GPU lock: one job at a time)
        │
        ▼
Moment selection: an LLM reads the whole transcript, scores segments,
and writes title / description / thumbnail text (Claude API)
        │
        ▼
Cutting & editing (ffmpeg: trim, concat, fades, automatic cold open)
        │
        ▼
Thumbnail (OpenCV face detection + vision-model frame choice + Pillow, Hebrew text)
        │
        ▼
Approval via Telegram bot (video preview on the phone)
        │
        ▼
Scheduled upload queue (YouTube Data API v3, OAuth2, quota-aware), credit + links to the creator
```

## Highlights

- **No manual editing.** A real 11.7-hour stream (29.9.2026) produced 11 candidate segments, 9 above the quality bar, 8 approved, 7 published. Stream end to first clip online: about 5 hours, on one home PC.
- **Cost control.** Prompt caching, the Batch API for non-urgent videos (50% cheaper), and a budget controller with `realtime` / `batch` / `waiting_budget` modes under a fixed monthly cap. Model cost per stream is $0.2-2 depending on length; the 11.7-hour stream above cost $1.84.
- **Quota-aware uploads.** A scheduled queue spreads uploads over the day within the YouTube API's 10,000-unit daily quota and puts time-sensitive clips first.
- **Fails loudly, not silently.** Partial failures (an untranscribed chunk, a window the model did not analyse, a clip that failed to cut) are recorded and reported instead of being treated as "no results".
- **Runs unattended.** A monitor loop polls every 5 minutes, with a separate watchdog started by the OS scheduler.

## Stack

Python · Anthropic Claude API · faster-whisper (CUDA) · ffmpeg · yt-dlp · OpenCV · Pillow · YouTube Data API v3 · Telegram Bot API

## Numbers so far (1.10.2026)

| Metric | Value |
|---|---|
| Creators in the watchlist | 18 (7 tracked automatically) |
| Long videos analysed | 16 |
| Candidate clips scored | 97 |
| Approved by a human | 37 |
| Uploaded to YouTube | 16 (15 public; channel opened 10.9.2026) |
| Transcription speed | ~6x real time (GTX 1650, 4GB) |
| API cost, September 2026 | ~$28 (from token logs; monthly cap $30) |

## Setup

Credentials are not included in this repository. To run it you need:

- `client_secret_*.json`: Google OAuth client for the YouTube Data API
- `telegram_config.json`: bot token and chat ID
- `ANTHROPIC_API_KEY` environment variable
- `pip install -r requirements.txt`
- ffmpeg and a CUDA-capable GPU
