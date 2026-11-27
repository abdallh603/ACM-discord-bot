"""Shared test fixtures for core and extension tests (import as tests.support)."""
import io

from PIL import Image


def problem(contest=100, index="A", rating=1200, tags=None):
    return {"contestId": contest, "index": index, "rating": rating, "name": "Example",
            "type": "PROGRAMMING", "tags": tags or ["dp"]}


def submission(stamp, *, verdict="OK", kind="PRACTICE", members=1, sid=1):
    return {"id": sid, "creationTimeSeconds": stamp, "verdict": verdict, "problem": problem(),
            "author": {"participantType": kind, "members": [{"handle": "tester"}] * members}}


def avatar(color):
    output = io.BytesIO()
    Image.new("RGB", (128, 128), color).save(output, format="PNG")
    return output.getvalue()
