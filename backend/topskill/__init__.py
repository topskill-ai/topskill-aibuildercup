"""TopSkill backend package."""

import os

__version__ = "0.1.0"

# P2 §18.3: a2a-sdk's own tracing decorators (read once, when ``a2a`` is first imported) emit ~60 spans per
# A2A call and leak their span context into ADK's run, so ADK spans would hang off a queue span instead of
# the call span. Our call span + ADK's spans already cover the call; set to "true" to get them back.
os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")
