from __future__ import annotations

CONTROL_PROMPT = """\
[Control Protocol]
If execution reaches a point where the user must complete provider authentication before you can continue, stop exploration immediately and emit exactly one control frame in this format:
<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"auth_required","provider":"<canonical-provider-name>","reason":"login_required"}}</OMA_CONTROL>

Rules:
- Do not add explanatory text before or after the control frame.
- Use canonical provider names such as bilibili, youtube, xiaohongshu, xianyu.
- Only use this control frame when authentication is genuinely required to proceed.
- If authentication is not required, continue normally.
"""


def inject_control_protocol(prompt: str) -> str:
    if CONTROL_PROMPT in prompt:
        return prompt
    return f"{CONTROL_PROMPT}\n{prompt}"
