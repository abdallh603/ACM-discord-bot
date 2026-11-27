import tempfile
import unittest
from pathlib import Path

from just_bot.activities_store import Activities
from just_bot.blitz import validate_options
from just_bot.core.storage import Store


class ActivityPersistenceTests(unittest.TestCase):
    def test_games_mute_ownership_and_goals_survive_reopening_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bot.sqlite3"
            store = Store(path)
            data = Activities(store)
            first = data.create_match(10, 1, validate_options({}), [(1, "one"), (2, "two"), (3, "three")])
            second = data.create_match(10, 4, validate_options({}), [(4, "four"), (5, "five")])
            data.update_match(first["id"], state="active", starts_at=100, ends_at=1000, voice_id=123)
            data.update_player(first["id"], 2, owns_mute=1, original_voice=456)
            data.save_goal(10, 1, "training", {"stage": 1, "handle": "one"})
            store.close()
            reopened = Store(path)
            try:
                data = Activities(reopened)
                self.assertEqual(data.match(10, first["id"])["ends_at"], 1000)
                self.assertEqual(data.match(10, second["id"])["state"], "pending")
                player = data.players(first["id"])[1]
                self.assertEqual((player["owns_mute"], player["original_voice"]), (1, 456))
                self.assertEqual(data.goal(10, 1, "training")["stage"], 1)
            finally:
                reopened.close()
