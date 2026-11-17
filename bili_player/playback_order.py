"""Shuffle queue identities without rearranging the visible queue."""
from __future__ import annotations

import random


class ShuffleOrder:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.bag = []
        self.history = []

    def reset(self):
        self.bag.clear()
        self.history.clear()

    def record(self, identity):
        if identity is not None and (not self.history or self.history[-1] != identity):
            self.history.append(identity)
        self.bag = [item for item in self.bag if item != identity]

    def next(self, identities, current):
        valid = list(dict.fromkeys(identities))
        if not valid:
            return None
        self.history = [item for item in self.history if item in valid]
        self.record(current if current in valid else None)
        self.bag = [item for item in self.bag if item in valid and item != current]
        if not self.bag:
            self.bag = [item for item in valid if item != current] or valid[:]
            self.rng.shuffle(self.bag)
        identity = self.bag.pop()
        self.record(identity)
        return identity

    def previous(self, identities, current):
        valid = set(identities)
        self.history = [item for item in self.history if item in valid]
        if self.history and self.history[-1] == current and len(self.history) > 1:
            self.history.pop()
        return self.history[-1] if self.history else (current if current in valid else next(iter(valid), None))
