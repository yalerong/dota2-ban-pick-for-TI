import pytest

from bp.draft_state import DraftState
from tests.test_pipeline import SEQ, make_match
from bp.normalize import draft_sequence

FMT = tuple(SEQ)
POOL = frozenset(range(1, 130))


def test_replay_real_sequence_matches_events():
    m = make_match(1, 1_710_000_000, first_team=1)  # dire acts first
    ev = draft_sequence(m["picks_bans"])
    st = DraftState.from_events(FMT, POOL, [(o, t, p, h) for o, t, p, h in ev])
    assert st.done and st.step == 24
    assert len(st.picks(0)) == 5 and len(st.picks(1)) == 5
    assert len(st.bans()) == 14 and not st.legal()


def test_turn_order_and_validation():
    st = DraftState(FMT, POOL)
    assert st.next_is_pick is False and st.next_team == 0 and st.phase == 0
    st.apply(5)
    with pytest.raises(ValueError):
        st.apply(5)                     # already used
    with pytest.raises(ValueError):
        st.apply(6, team=0)             # wrong team
    with pytest.raises(ValueError):
        st.apply(6, is_pick=True)       # ban phase
    st.apply(6)
    assert st.step == 2 and 5 not in st.legal()
    st.undo()
    assert st.step == 1 and 6 in st.legal()
    for h in range(10, 16):
        st.apply(h)
    assert st.step == 7 and st.next_is_pick is True and st.phase == 1
    assert st.prefix_key(2) == ((0, 0, 5), (0, 1, 10))
