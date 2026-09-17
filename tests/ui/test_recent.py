"""The scaled-art cache's eviction, which is the part worth pinning.

It exists because the picker rescaled every visible cover on every frame:
measured on a Steam Deck, 7.4 ms of a 16.7 ms frame for ten covers. Bounded
because a library here is nine thousand games and an unbounded one would hold
a surface for every cover ever looked at.
"""

from gotg_ui.recent import Recent


def test_it_keeps_what_it_is_given():
    cache = Recent(4)
    cache.put("a", 1)
    assert cache.get("a") == 1
    assert "a" in cache


def test_a_miss_is_none_rather_than_an_error():
    assert Recent().get("nothing") is None


def test_it_stops_at_its_capacity():
    cache = Recent(3)
    for i in range(10):
        cache.put(i, i)
    assert len(cache) == 3


def test_the_oldest_goes_first():
    cache = Recent(3)
    for key in "abc":
        cache.put(key, key)
    cache.put("d", "d")
    assert cache.get("a") is None
    assert cache.get("b") == "b"


def test_looking_at_something_keeps_it():
    """Least recently *used*, not least recently added.

    The cover under the cursor is asked for every frame. Keyed on insertion
    alone it would age out while somebody was looking straight at it.
    """
    cache = Recent(3)
    for key in "abc":
        cache.put(key, key)
    cache.get("a")          # still wanted
    cache.put("d", "d")     # evicts something
    assert cache.get("a") == "a"
    assert cache.get("b") is None


def test_putting_the_same_key_twice_does_not_use_two_places():
    cache = Recent(2)
    cache.put("a", 1)
    cache.put("a", 2)
    cache.put("b", 3)
    assert len(cache) == 2
    assert cache.get("a") == 2


def test_put_hands_the_value_back():
    # So a caller can scale, store and blit in one line rather than three.
    cache = Recent()
    assert cache.put("k", "surface") == "surface"


def test_a_capacity_of_nothing_is_still_a_capacity_of_one():
    # Rather than a cache that divides by zero or keeps nothing and thrashes.
    cache = Recent(0)
    cache.put("a", 1)
    assert cache.get("a") == 1


def test_clearing_forgets_everything():
    cache = Recent()
    cache.put("a", 1)
    cache.clear()
    assert len(cache) == 0
