import unittest

from extensions.blitz.rooms import choose_voice_room, owns_room
from just_bot.core.domain import UserError


class RoomPolicyTests(unittest.TestCase):
    def test_a_players_existing_room_takes_priority(self):
        self.assertEqual(choose_voice_room([10, 20, 30], [20, 30]), 20)

    def test_an_existing_available_room_is_used_before_creating_one(self):
        self.assertEqual(choose_voice_room([10, 20], []), 10)
        self.assertIsNone(choose_voice_room([], []))

    def test_explicit_room_has_priority_over_current_voice(self):
        self.assertEqual(choose_voice_room([10, 20], [10], requested_id=20), 20)

    def test_unavailable_explicit_room_does_not_silently_create_another(self):
        with self.assertRaises(UserError):
            choose_voice_room([10], [10], requested_id=99)

    def test_reused_rooms_are_never_owned_and_legacy_rooms_remain_recoverable(self):
        self.assertFalse(owns_room({"options": {"owns_voice_channel": False}}, "voice"))
        self.assertFalse(owns_room({"options": {"owns_text_channel": False}}, "text"))
        self.assertTrue(owns_room({"options": {}}, "voice"))
