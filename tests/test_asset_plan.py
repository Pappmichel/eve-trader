from eve_trader.production.engine import _allocate_scarce_stock


def test_allocate_scarce_stock_covers_everyone_when_abundant():
    claims = [(1, 30.0), (2, 50.0)]
    covered = _allocate_scarce_stock(claims, available=1000.0)
    assert covered == {1: 30.0, 2: 50.0}


def test_allocate_scarce_stock_fills_smallest_claims_first():
    # 3 jobs want 10/40/100 of a scarce component; only 60 in stock - the two
    # smallest claims should come out fully covered (10 + 40 = 50), and the
    # remaining 10 goes toward the big claim, which stays short.
    claims = [(1, 100.0), (2, 10.0), (3, 40.0)]
    covered = _allocate_scarce_stock(claims, available=60.0)
    assert covered[2] == 10.0
    assert covered[3] == 40.0
    assert covered[1] == 10.0


def test_allocate_scarce_stock_zero_available_covers_nothing():
    claims = [(1, 5.0), (2, 1.0)]
    covered = _allocate_scarce_stock(claims, available=0.0)
    assert covered == {1: 0.0, 2: 0.0}


def test_allocate_scarce_stock_maximizes_count_of_full_claims_over_proportional_split():
    # A proportional split (60% of demand covered) would give everyone 60% -
    # nobody complete. The confirmed rule instead fully satisfies as many
    # whole claims as possible: with 100 in stock across claims of 20/30/80,
    # the two small ones (20+30=50) are fully covered, leaving 50 of the 100
    # stock for the 80-claim - still short, but every unit is used.
    claims = [(1, 80.0), (2, 20.0), (3, 30.0)]
    covered = _allocate_scarce_stock(claims, available=100.0)
    assert covered[2] == 20.0
    assert covered[3] == 30.0
    assert covered[1] == 50.0
    assert sum(covered.values()) == 100.0
