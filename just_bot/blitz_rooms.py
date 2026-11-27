"""Room selection policy, independent of Discord network operations."""
from .core.domain import UserError


def choose_voice_room(available_ids, occupied_ids, requested_id=None):
    """Prefer an explicit eligible room, then a player's room, then another existing room."""
    available = list(dict.fromkeys(available_ids))
    if requested_id is not None:
        if requested_id not in available:
            raise UserError("The selected voice room is unavailable or not accessible to every player.")
        return requested_id
    for channel_id in occupied_ids:
        if channel_id in available:
            return channel_id
    return available[0] if available else None


def owns_room(match, kind):
    # Matches saved before room reuse was introduced always created their own rooms.
    return match["options"].get(f"owns_{kind}_channel", True)
