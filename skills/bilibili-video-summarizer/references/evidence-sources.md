# Bilibili Evidence Sources

Use this reference only when you need Bilibili-specific retrieval hints or need to explain evidence quality.

## URL Forms

- Canonical desktop video URL: `https://www.bilibili.com/video/BV.../`
- Legacy numeric ID form: `https://www.bilibili.com/video/av123456/`
- Mobile page: `https://m.bilibili.com/video/BV...`
- Short link: `https://b23.tv/...`
- Multi-part selector: `?p=<number>`
- Timestamp hint: `?t=<seconds>`
- Common tracking parameters to ignore: `vd_source`, `spm_id_from`, `from_spmid`, `share_source`

Normalize short links and mobile links before summarizing when possible.

If the user pastes a Bilibili domain without a scheme, treat it as the same URL with `https://`.

## Page Text Cues

Prioritize page text that is directly tied to the target video or part:

- title
- uploader name
- description
- part title for the requested `p=`
- chapter markers
- tags
- publish date
- uploader-owned summary text shown on the video page

Treat these as metadata, not as proof of every spoken claim in the video.

## Evidence Priority

Use sources in this order:

1. User-provided transcript, subtitle text, OCR, or notes
2. Public subtitle or transcript text tied to the exact Bilibili video or part
3. Official page metadata:
   - title
   - uploader name
   - description
   - part title
   - chapters
   - tags
   - publish date
4. Other uploader-owned text about the same video

Treat anything below that line as weak context only:

- danmaku
- comments
- recommendation cards
- third-party repost summaries
- unrelated search results

## Multi-Part Videos

- Treat each `p=` value as a separate summary target unless the user asks for the whole upload.
- Preserve the part title when available.
- Do not combine multiple parts into one summary without saying so.

## Ambiguous Targets

Treat these as incomplete targets until the exact video item is identified:

- collection or series landing pages
- creator profile pages
- favorites pages
- search result pages
- repost or clip pages that do not clearly match the requested original

If the user provided one of these without a specific item, ask which exact video to summarize.

## Timestamps

Use timestamps only when they come from:

- transcript text
- subtitle files
- official chapter markers
- the user's notes or screenshots

Do not invent timestamps from memory, video title wording, or comment guesses.

## Common Failure Modes

- The page loads but exposes only title and description.
- Subtitle access is missing, region-locked, login-gated, or blocked by anti-bot controls.
- The user provides a short link that tools cannot resolve.
- The link points to a collection or series page instead of a single video part.
- Network or browser access is unavailable in the current environment.

When this happens, fall back to a clearly labeled metadata summary or ask for transcript text if that is the only practical unblocker.

## What Not To Do

- Do not say you watched the video if you only read metadata.
- Do not treat danmaku or comments as factual proof of the spoken content.
- Do not use private cookies or personal login sessions.
- Do not overfit the summary to popularity metrics.
- Do not expand a title-only result into a fake detailed recap.
