"""Assemble /help from ordered entries contributed by core and each enabled extension.

The description is a series of blocks separated by blank lines. Each block holds ordered lines.
Some lines are composite: their items (e.g. the organizer's ``/admin`` commands) come from several
owners and are joined into one line. Empty blocks and composite lines are omitted.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

Text = str | Callable[[], str]

# Generic sections; extensions may also name a new block (it sorts before "organizers").
BLOCKS = {"intro": 0, "commands": 10, "games": 20, "events": 30, "voice": 40, "organizers": 100}
NEW_BLOCK_ORDER = 50


@dataclass
class Composite:
    block: str
    order: int
    prefix: str = ""
    joiner: str = ", "
    suffix: str = ""
    items: list = field(default_factory=list)

    def render(self):
        texts = [_text(text) for _, _, text in sorted(self.items, key=lambda item: item[:2])]
        return self.prefix + self.joiner.join(texts) + self.suffix if texts else None


def _text(value: Text) -> str:
    return value() if callable(value) else value


class HelpBook:
    def __init__(self):
        self._lines: list[tuple[str, int, int, Text]] = []
        self._composites: dict[str, Composite] = {}
        self._blocks: dict[str, tuple[int, int]] = {name: (order, 0) for name, order in BLOCKS.items()}
        self._seq = 0

    def _next(self):
        self._seq += 1
        return self._seq

    def _block(self, name: str):
        if name not in self._blocks:
            self._blocks[name] = (NEW_BLOCK_ORDER, self._next())

    def line(self, text: Text, *, block: str, order: int = 1000):
        self._block(block)
        self._lines.append((block, order, self._next(), text))

    def composite(self, name: str, *, block: str, order: int, prefix: str = "", joiner: str = ", ",
                  suffix: str = ""):
        self._block(block)
        self._composites[name] = Composite(block, order, prefix, joiner, suffix)

    def item(self, text: Text, *, line: str, order: int = 1000):
        if line not in self._composites:
            raise KeyError(f"Unknown /help line '{line}'. Known lines: {', '.join(sorted(self._composites))}")
        self._composites[line].items.append((order, self._next(), text))

    def render(self) -> str:
        rows: dict[str, list[tuple[int, int, str]]] = {}
        for block, order, seq, text in self._lines:
            rows.setdefault(block, []).append((order, seq, _text(text)))
        for name, composite in self._composites.items():
            rendered = composite.render()
            if rendered is not None:
                rows.setdefault(composite.block, []).append((composite.order, 0, rendered))
        blocks = sorted(rows, key=lambda name: self._blocks[name])
        return "\n\n".join("\n".join(text for *_, text in sorted(rows[name])) for name in blocks)
