import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from extensions.tournament.image import TournamentPictures, bracket_nodes, render_tournament, status
from tests.support import avatar


def bracket(count=8):
    entries = [{"user_id": user, "handle": f"cf{user}"} for user in range(1, count + 1)]
    pairs = [{"round_no": 1, "position": p, "player_a": p * 2 - 1, "player_b": p * 2,
              "winner_id": None, "state": "playing", "note": "", "blitz_id": f"game{p}"}
             for p in range(1, count // 2 + 1)]
    tournament = {"id": "sample", "guild_id": 10, "name": "Community Cup", "state": "running",
                  "capacity": count, "champion_id": None}
    identities = {e["user_id"]: {"username": f"discord{e['user_id']}", "handle": e["handle"]}
                  for e in entries}
    return tournament, entries, pairs, identities


class TournamentImageTests(unittest.IsolatedAsyncioTestCase):
    def test_known_winners_propagate_without_inventing_results_or_future_opponents(self):
        tournament, entries, pairs, identities = bracket()
        pairs[0].update(state="done", winner_id=1)
        columns = bracket_nodes(pairs)
        self.assertEqual([len(c) for c in columns], [4, 2, 1])
        self.assertEqual(columns[1][0]["player_a"], 1)
        self.assertIsNone(columns[1][0]["player_b"])
        self.assertIsNone(columns[1][0]["winner_id"])
        self.assertTrue(all(c["player_a"] is None for c in columns[-1]))

    def test_match_status_comes_from_live_game_and_manual_decisions_are_distinct(self):
        _, _, pairs, _ = bracket(2)
        node = pairs[0]
        for state, expected in (("pending", "ACCEPT INVITE"), ("countdown", "COUNTDOWN"),
                                ("active", "LIVE"), ("settling", "VERIFYING")):
            self.assertEqual(status(node, {node["blitz_id"]: state})[0], expected)
        node.update(state="done", winner_id=1, note="Manager decision by 99")
        self.assertEqual(status(node, {})[0], "MANAGER DECISION")

    def test_actual_avatars_appear_on_each_side_of_the_player_label(self):
        tournament, entries, pairs, identities = bracket(2)
        identities[1].update(discord_avatar=avatar("red"), cf_avatar=avatar("blue"))
        with Image.open(render_tournament(tournament, entries, pairs, identities)) as image:
            # First row of first match: Discord on the left, CF on the right.
            self.assertEqual(image.getpixel((75, 248)), (255, 0, 0))
            self.assertEqual(image.getpixel((405, 248)), (0, 0, 255))

    def test_registration_byes_finished_and_maximum_brackets_render_valid_bounded_pngs(self):
        for count in (2, 4, 8, 16, 32):
            tournament, entries, pairs, identities = bracket(count)
            if count == 4:
                pairs[0].update(player_b=None, state="done", winner_id=1, note="Bye")
                entries = [e for e in entries if e["user_id"] != 2]
            if count == 2:
                tournament.update(state="finished", champion_id=1)
                pairs[0].update(state="done", winner_id=1)
            output = render_tournament(tournament, entries, pairs, identities)
            self.assertLess(len(output.getvalue()), 8 * 1024 * 1024)
            with Image.open(output) as image:
                self.assertLessEqual(max(image.size), 4096)
                image.verify()
        tournament["state"] = "registration"
        with Image.open(render_tournament(tournament, entries, [], identities)) as image:
            self.assertEqual(image.width, 1040)

    async def test_real_identity_fetches_are_batched_cached_and_use_small_memory_thumbnails(self):
        asset = SimpleNamespace(read=AsyncMock(return_value=avatar("red")))
        asset.with_size = lambda size: asset
        asset.with_static_format = lambda format: asset
        member = SimpleNamespace(name="discord_actual", display_avatar=asset)
        guild = SimpleNamespace(get_member=lambda uid: member)
        cf = SimpleNamespace(users=AsyncMock(return_value=[{"handle": "cf1", "avatar": "https://userpic.codeforces.org/a.png"}]))
        pictures = TournamentPictures(SimpleNamespace(get_guild=lambda gid: guild, cf=cf))
        tournament, entries, pairs, identities = bracket(2)
        with patch("extensions.tournament.image.fetch_avatar", return_value=avatar("blue")):
            first = await pictures.identities(tournament, entries[:1])
            second = await pictures.identities(tournament, entries[:1])
        self.assertEqual(first, second)
        self.assertEqual(first[1]["username"], "discord_actual")
        cf.users.assert_awaited_once_with(["cf1"])
        with Image.open(io.BytesIO(first[1]["cf_avatar"])) as photo:
            self.assertEqual(photo.size, (64, 64))

    async def test_identity_failures_preserve_bracket_and_verified_handle(self):
        cf = SimpleNamespace(users=AsyncMock(side_effect=OSError("offline")))
        pictures = TournamentPictures(SimpleNamespace(get_guild=lambda gid: None, cf=cf))
        tournament, entries, _, _ = bracket(2)
        identities = await pictures.identities(tournament, entries)
        self.assertEqual(identities[1]["handle"], "cf1")
        self.assertEqual(identities[2]["handle"], "cf2")
