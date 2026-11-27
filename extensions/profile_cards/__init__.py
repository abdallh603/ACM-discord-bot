"""Profile cards: render the member's Discord and Codeforces avatars into /handle show."""
from __future__ import annotations

from .render import ProfileCards


def setup(ext):
    cards = ProfileCards()

    async def profile_image(interaction, member, profile):
        # Without Attach Files permission the ordinary profile embed is sent instead.
        if not interaction.app_permissions.attach_files:
            return None
        try:
            return await cards.build(member, profile)
        except (TimeoutError, OSError, ValueError):
            return None

    ext.hook("profile_image", profile_image)
    return cards
