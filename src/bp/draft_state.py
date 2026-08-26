"""Captain's Mode draft state machine, parameterized by a data-inferred format (P2-06)."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Action:
    order: int
    team: int      # 0 = first-acting team ("us" if we act first), 1 = other
    is_pick: bool
    hero_id: int


@dataclass
class DraftState:
    fmt: tuple[tuple[int, int], ...]          # [(is_pick, team_rel)] from draft_formats
    hero_pool: frozenset[int]
    actions: list[Action] = field(default_factory=list)

    # ---- queries ------------------------------------------------------
    @property
    def step(self) -> int:
        return len(self.actions)

    @property
    def done(self) -> bool:
        return self.step >= len(self.fmt)

    @property
    def next_is_pick(self) -> bool | None:
        return None if self.done else bool(self.fmt[self.step][0])

    @property
    def next_team(self) -> int | None:
        return None if self.done else self.fmt[self.step][1]

    @property
    def phase(self) -> int:
        """0-based index of the current run of same-type actions."""
        if not self.fmt:
            return 0
        ph, prev = -1, None
        for i, (is_pick, _) in enumerate(self.fmt[: min(self.step + 1, len(self.fmt))]):
            if is_pick != prev:
                ph += 1
                prev = is_pick
        return ph

    def used(self) -> set[int]:
        return {a.hero_id for a in self.actions}

    def picks(self, team: int) -> list[int]:
        return [a.hero_id for a in self.actions if a.is_pick and a.team == team]

    def bans(self, team: int | None = None) -> list[int]:
        return [a.hero_id for a in self.actions if not a.is_pick and (team is None or a.team == team)]

    def legal(self) -> set[int]:
        return set() if self.done else set(self.hero_pool) - self.used()

    def remaining(self) -> list[tuple[int, int]]:
        return list(self.fmt[self.step:])

    def prefix_key(self, k: int | None = None) -> tuple[tuple[int, int, int], ...]:
        """(is_pick, team, hero) prefix, used to look up opponent_response_edges."""
        acts = self.actions if k is None else self.actions[:k]
        return tuple((int(a.is_pick), a.team, a.hero_id) for a in acts)

    # ---- transitions --------------------------------------------------
    def apply(self, hero_id: int, team: int | None = None, is_pick: bool | None = None) -> "DraftState":
        if self.done:
            raise ValueError("draft complete")
        exp_pick, exp_team = self.fmt[self.step]
        if team is not None and team != exp_team:
            raise ValueError(f"step {self.step}: expected team {exp_team}, got {team}")
        if is_pick is not None and bool(is_pick) != bool(exp_pick):
            raise ValueError(f"step {self.step}: expected {'pick' if exp_pick else 'ban'}")
        if hero_id not in self.hero_pool:
            raise ValueError(f"unknown hero {hero_id}")
        if hero_id in self.used():
            raise ValueError(f"hero {hero_id} already picked/banned")
        self.actions.append(Action(self.step, exp_team, bool(exp_pick), hero_id))
        return self

    def undo(self) -> "DraftState":
        if self.actions:
            self.actions.pop()
        return self

    @classmethod
    def from_events(cls, fmt, hero_pool, events: list[tuple[int, int, int, int]]) -> "DraftState":
        """Replay draft_events rows (order, team_side, is_pick, hero_id). Sides are re-based to the first actor."""
        st = cls(fmt, frozenset(hero_pool))
        first = events[0][1] if events else 0
        for _, side, is_pick, hero in sorted(events):
            st.apply(hero, team=0 if side == first else 1, is_pick=bool(is_pick))
        return st
