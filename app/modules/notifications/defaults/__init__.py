"""Default notification preferences and templates — hardcoded code-level fallbacks.

Preferences: code defaults for INTERNAL and RECIPIENT channel toggles.
Templates:   default template content for all event/type/channel combinations.

DB overrides take priority. Reset = delete DB rows, code defaults take over.
"""

from app.modules.notifications.defaults.preferences import (
    ChannelDefaults,
    get_event_channel_default,
    get_event_defaults,
)
from app.modules.notifications.defaults.templates import (
    get_default_template,
    get_hardcoded_for_context,
    get_hardcoded_variables,
)

__all__ = [
    "ChannelDefaults",
    "get_default_template",
    "get_event_channel_default",
    "get_event_defaults",
    "get_hardcoded_for_context",
    "get_hardcoded_variables",
]
