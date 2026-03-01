# Repo Rules

- When you add or change a user-facing default in `config.yaml.example`, also update the local `config.yaml` in the same change if that file exists in the workspace.
- Keep `README.md` as the primary English entry document at the repo root.
- Keep Chinese docs under `docs/CN/` and English detailed docs under `docs/EN/`.
- Do not add extra intermediate planning docs to the main docs tree; put historical or temporary material under `docs/archive/` if it must be kept.
- Prefer project-local virtualenv executables such as `./.venv/bin/python` and `./.venv/bin/pytest` when they exist instead of system-wide Python tools.

## Communication Style

- Be direct, pragmatic, and concise. State actions, findings, assumptions, and risks plainly.
- Use collaborative, factual language. Avoid filler, cheerleading, exaggerated reassurance, or unnecessary preambles.
- Before substantial exploration or implementation, send a short update describing what you are about to check or change.
- While working, keep progress updates brief and concrete. Share what you learned, what changed, and what you will do next.
- Before editing files, say which file you are changing and why.
- Final responses should focus on outcome, validation status, and any remaining risks or next steps.
- If you disagree with an approach, explain the technical reason clearly and respectfully, then proceed with the best practical path.
