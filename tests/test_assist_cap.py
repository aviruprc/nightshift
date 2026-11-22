"""The CAPTCHA helper opens tabs at a pace a human can solve, never the whole queue at once.

data/assist_queue.txt held 20 ids and the helper opened a visible tab for each of them within a minute
(8 rows parked "CAPTCHA tab is open and pre-filled" between and). Every idle tab is memory plus a page that
expires before she reaches it, and 20 tabs is not a hand-off. The rest wait in the queue and open as earlier tabs close.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import assist  # noqa: E402


def test_no_more_tabs_than_the_cap():
    assert assist.pending_slots(["a", "b", "c", "d", "e"], set(), set(), cap=3) == ["a", "b", "c"]


def test_room_shrinks_as_tabs_open():
    assert assist.pending_slots(["a", "b", "c", "d"], set(), {"a", "b"}, cap=3) == ["c"]


def test_a_full_window_opens_nothing():
    assert assist.pending_slots(["a", "b"], set(), {"x", "y", "z"}, cap=3) == []


def test_a_handled_or_open_id_is_never_opened_twice():
    assert assist.pending_slots(["a", "b", "c"], {"a"}, {"b"}, cap=3) == ["c"]
