"""Blitz test fixtures, also used by extensions that depend on blitz."""
from extensions.blitz.engine import TERMINAL
from extensions.blitz.store import MIGRATIONS, BlitzStore
from just_bot.core.domain import UserError
from just_bot.core.schema import migrate


def blitz_store(store):
    """A BlitzStore on a core Store's database, with the blitz tables created."""
    migrate(store.db, "blitz", MIGRATIONS)
    return BlitzStore(store.db)


class FakeVoice:
    """Models Discord effects while persisting ownership in the real SQLite store."""
    def __init__(self, data):
        self.data = data
        self.muted = set()
        self.deafened = set()
        self.disconnected = set()
        self.fail_enter = None
        self.counter = 500

    async def preflight(self, match):
        pass

    async def create_rooms(self, match):
        self.counter += 2
        self.data.update_match(match["id"], text_id=self.counter, voice_id=self.counter + 1)

    async def enter(self, match, player):
        user = player["user_id"]
        if self.fail_enter == user:
            raise UserError("Missing voice permission")
        if user not in self.disconnected and match["options"]["mute_voice"] and user not in self.muted:
            self.data.update_player(match["id"], user, owns_mute=1)
            self.muted.add(user)
        if user not in self.disconnected and match["options"].get("deaf_voice") and user not in self.deafened:
            self.data.update_player(match["id"], user, owns_deaf=1)
            self.deafened.add(user)

    async def announce_start(self, match):
        pass

    async def countdown(self, match):
        pass

    async def cleanup(self, match):
        release_all = match["state"] in TERMINAL or match["state"] == "settling"
        for player in self.data.players(match["id"]):
            user = player["user_id"]
            if player["owns_mute"] and user not in self.disconnected and (release_all or player["solved_at"] is not None):
                self.muted.discard(user)
                self.data.update_player(match["id"], user, owns_mute=0)
            if player["owns_deaf"] and user not in self.disconnected and (release_all or player["solved_at"] is not None):
                self.deafened.discard(user)
                self.data.update_player(match["id"], user, owns_deaf=0)
        if match["state"] in TERMINAL:
            self.data.update_match(match["id"], announced=1)
