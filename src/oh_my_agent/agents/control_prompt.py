from __future__ import annotations

CONTROL_PROMPT = """\
[Control Protocol]
If execution reaches a point where the user must complete provider authentication before you can continue, stop exploration immediately and emit exactly one control frame in this format:
<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"auth_required","provider":"<canonical-provider-name>","reason":"login_required"}}</OMA_CONTROL>

If execution reaches a point where you cannot continue without the user choosing one option from a small fixed set, emit exactly one control frame in this format:
<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","question":"<clear question>","details":"<optional short context>","choices":[{"id":"<stable-machine-id>","label":"<short button label>","description":"<optional short explanation>"}]}}</OMA_CONTROL>

Rules:
- Do not add explanatory text before or after the control frame.
- Use canonical provider names such as bilibili, youtube, xiaohongshu, xianyu.
- Only use this control frame when authentication is genuinely required to proceed.
- Only use ask_user when execution truly depends on a single explicit user choice.
- ask_user choices must be a small fixed list; do not ask for free text.
- ask_user choice ids must be stable machine ids (slug-like strings).
- ask_user must include between 1 and 5 choices.
- Do not emit multiple control frames.
- If authentication is not required, continue normally.
"""


def inject_control_protocol(prompt: str) -> str:
    if CONTROL_PROMPT in prompt:
        return prompt
    return f"{CONTROL_PROMPT}\n{prompt}"
