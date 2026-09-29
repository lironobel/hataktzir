# Hataktzir: Automated Livestream Clipping Pipeline

An end-to-end Python pipeline that turns long livestreams into ready-to-publish YouTube clips. It detects new streams, transcribes them on a local GPU, lets an LLM pick the strongest moments, cuts and packages each clip with a title, description and thumbnail, and uploads it. The only human step is a one-tap approval in Telegram.

## How it works

```
Kick live/VOD detection
        │
        ▼
Download (yt-dlp, HLS)
        │
        ▼
Transcription (faster-whisper on CUDA, GPU lock: one job at a time)
        │
        ▼
Segment selection + title / description / thumbnail text (Claude API)
        │
        ▼
Cutting & editing (ffmpeg: trim, concat, fades, automatic cold open)
        │
        ▼
Thumbnail (OpenCV face detection + Pillow, Hebrew text)
        │
        ▼
Approval via Telegram bot (video preview on the phone)
        │
        ▼
Scheduled upload queue (YouTube Data API v3, OAuth2, quota-aware)
```

## Highlights

- **Fully automatic after approval.** An 88-minute stream produced 10 candidate segments with zero manual editing.
- **Cost control.** Prompt caching, the Batch API for non-urgent streams (50% cheaper), and a budget controller with `realtime` / `batch` / `waiting_budget` modes keep the cost at roughly $1.5 per stream.
- **Quota-aware uploads.** A scheduled queue manages the YouTube API's 10,000-unit daily quota.
- **Validated selection.** On a test stream, the system independently picked the same moment a professional editor chose; that clip reached 14K views.
- **Runs unattended.** A monitor loop polls every 5 minutes, with a separate watchdog started by the OS scheduler.

## Stack

Python · Anthropic Claude API · faster-whisper (CUDA) · ffmpeg · yt-dlp · OpenCV · Pillow · YouTube Data API v3 · Telegram Bot API

## Numbers so far

| Metric | Value |
|---|---|
| Streamers tracked | 14+ |
| Streams processed | 17 |
| Clips generated | 31 |
| Transcription speed | ~6x real time (GTX 1650, 4GB) |
| API cost, September | $14.79 |

## Setup

Credentials are not included in this repository. To run it you need:

- `client_secret_*.json`: Google OAuth client for the YouTube Data API
- `telegram_config.json`: bot token and chat ID
- `ANTHROPIC_API_KEY` environment variable
- `pip install -r requirements.txt`
- ffmpeg and a CUDA-capable GPU
