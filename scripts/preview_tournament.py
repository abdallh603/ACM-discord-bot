"""Create an illustrative eight-player bracket, without accessing Discord or real user data."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from just_bot.tournament_image import render_tournament


def sample():
    names = [("ahmad", "Ahmad_CF"), ("sara", "Sara_Codes"), ("omar", "Omar_CF"),
             ("lina", "Lina_Codes"), ("yousef", "Yousef_CF"), ("noor", "Noor_Codes"),
             ("zaid", "Zaid_CF"), ("hala", "Hala_Codes")]
    identities = {i: {"username": name, "handle": handle} for i, (name, handle) in enumerate(names, 1)}
    entries = [{"user_id": i, "handle": identity["handle"]} for i, identity in identities.items()]
    pairs = []
    for position, (a, b, winner) in enumerate(((1, 2, 1), (3, 4, 4), (5, 6, 6), (7, 8, 8)), 1):
        pairs.append({"round_no": 1, "position": position, "player_a": a, "player_b": b,
                      "winner_id": winner, "state": "done", "note": "Verified Blitz result.", "blitz_id": None})
    for position, (a, b) in enumerate(((1, 4), (6, 8)), 1):
        pairs.append({"round_no": 2, "position": position, "player_a": a, "player_b": b,
                      "winner_id": None, "state": "playing", "note": "", "blitz_id": f"demo{position}"})
    tournament = {"name": "Community Cup · Example bracket", "id": "SAMPLE", "state": "running",
                  "capacity": 8, "champion_id": None}
    return tournament, entries, pairs, identities, {"demo1": "active", "demo2": "pending"}


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "docs" / "tournament-bracket-preview.png"
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(render_tournament(*sample()).getvalue())
    print(destination)
