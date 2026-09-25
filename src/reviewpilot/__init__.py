"""ReviewPilot - AI code review with sandbox verification.

The only AI code reviewer that proves its suggestions work before posting them.
"""
__version__ = "0.1.0"

from reviewpilot.config import ReviewPilotConfig, load_config

__all__ = [
    "__version__",
    "ReviewPilotConfig",
    "load_config",
]
