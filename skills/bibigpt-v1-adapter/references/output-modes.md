# BibiGPT-Style Output Modes

Use the lightest format that satisfies the request. Do not inflate a weak source into a deep note.

## Quick Digest

Use when the user wants a fast skim, a chat reply, or a mobile-friendly note.

```md
# <Title>

- Source: <url or source note>
- Evidence: transcript-backed | metadata-backed | insufficient-evidence

## TL;DR

<2-4 sentences>

## Key Points

- ...
- ...

## Evidence Used

- ...
```

## Standard Brief

Use for the default BibiGPT-style result.

```md
# <Title>

- Source: <url or source note>
- Evidence: transcript-backed | metadata-backed | insufficient-evidence

## TL;DR

<short paragraph>

## Outline

- ...
- ...

## Key Insights

- ...
- ...

## Action Items / Open Questions

- ...

## Evidence Used

- ...
```

## Study Note

Use when the user wants learning-oriented output, revision notes, or a saved Markdown file.

```md
# <Title>

- Source: <url or source note>
- Evidence: transcript-backed | metadata-backed | insufficient-evidence

## Core Summary

<1-3 short paragraphs>

## Content Flow

- ...
- ...

## Important Ideas

- ...
- ...

## Terms, Quotes, or Timestamps

- Include only verified items

## Questions To Revisit

- ...

## Evidence Used

- ...
```

## Flashcards

Use only when the source text is strong enough to support specific recall prompts.

```md
## Flashcards

- Q: ...
  A: ...
- Q: ...
  A: ...
```

Avoid flashcards for metadata-backed results unless the prompts are clearly about the visible metadata itself.

## Metadata-Only Fallback

When only title, description, chapters, or uploader text is available:

- Keep the summary short
- Say it is based on metadata rather than full content
- Do not invent detailed arguments, quotes, or timestamps
