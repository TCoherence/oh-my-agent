from __future__ import annotations

import warnings

from oh_my_agent.agents.base import BaseAgent


class BaseAPIAgent(BaseAgent):
    """Base class for agents that call model APIs directly.

    .. deprecated:: 0.4.0
        API agents are deprecated in favour of CLI agents which provide a
        complete agentic loop (tools, skills, context).  This module will be
        removed in a future release.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        warnings.warn(
            f"{cls.__name__} inherits from BaseAPIAgent which is deprecated "
            "since v0.4.0. Use a CLI agent instead.",
            DeprecationWarning,
            stacklevel=2,
        )
