from app.candlestick import (
    Candle, hammer, inverted_hammer, dragonfly_doji, bullish_engulfing, bullish_harami,
    piercing_line, tweezer_bottom, morning_star, three_white_soldiers, resample, scan,
)


def C(o, h, l, c, ts=0.0, v=100.0):
    return Candle(start_ts=ts, open=o, high=h, low=l, close=c, volume=v)


# --- hammer / inverted hammer ---

def test_hammer_matches_classic_shape():
    # small body near top, long lower wick, negligible upper wick
    c = C(o=10.0, h=10.05, l=9.0, c=10.02)
    m = hammer(c)
    assert m is not None
    assert m.name == "HAMMER"
    assert 0 < m.confidence <= 1.0


def test_hammer_rejects_large_body():
    c = C(o=9.0, h=10.05, l=8.9, c=10.0)  # body dominates the range
    assert hammer(c) is None


def test_hammer_rejects_large_upper_wick():
    c = C(o=9.95, h=10.5, l=9.0, c=10.02)  # meaningful upper wick too
    assert hammer(c) is None


def test_inverted_hammer_matches():
    c = C(o=10.0, h=11.0, l=9.98, c=10.05)
    m = inverted_hammer(c)
    assert m is not None
    assert m.name == "INVERTED_HAMMER"


def test_dragonfly_doji_matches():
    c = C(o=10.0, h=10.02, l=9.0, c=10.01)
    m = dragonfly_doji(c)
    assert m is not None


def test_dragonfly_doji_rejects_real_body():
    c = C(o=9.5, h=10.02, l=9.0, c=10.0)
    assert dragonfly_doji(c) is None


# --- bullish engulfing ---

def test_bullish_engulfing_matches():
    prev = C(o=10.0, h=10.1, l=9.5, c=9.6)   # bearish
    cur = C(o=9.55, h=10.3, l=9.5, c=10.2)   # bullish, engulfs prev body
    m = bullish_engulfing(prev, cur)
    assert m is not None
    assert m.name == "BULLISH_ENGULFING"


def test_bullish_engulfing_rejects_partial_engulf():
    prev = C(o=10.0, h=10.1, l=9.5, c=9.6)
    cur = C(o=9.7, h=10.05, l=9.6, c=10.0)  # bullish but does not fully engulf
    assert bullish_engulfing(prev, cur) is None


def test_bullish_engulfing_rejects_wrong_direction():
    prev = C(o=9.6, h=10.1, l=9.5, c=10.0)  # bullish, wrong for this pattern
    cur = C(o=10.0, h=10.3, l=9.4, c=9.5)
    assert bullish_engulfing(prev, cur) is None


# --- bullish harami ---

def test_bullish_harami_matches():
    prev = C(o=10.0, h=10.1, l=9.0, c=9.2)   # large bearish
    cur = C(o=9.3, h=9.5, l=9.25, c=9.45)    # small bullish, inside prev body
    m = bullish_harami(prev, cur)
    assert m is not None


def test_bullish_harami_rejects_when_not_contained():
    prev = C(o=10.0, h=10.1, l=9.0, c=9.2)
    cur = C(o=9.1, h=10.5, l=9.0, c=10.4)  # not contained
    assert bullish_harami(prev, cur) is None


# --- piercing line ---

def test_piercing_line_matches():
    prev = C(o=10.0, h=10.1, l=9.0, c=9.2)     # bearish, midpoint = 9.6
    cur = C(o=8.8, h=9.9, l=8.7, c=9.8)        # gaps below prev low, closes above midpoint, below prev open
    m = piercing_line(prev, cur)
    assert m is not None


def test_piercing_line_rejects_no_gap():
    prev = C(o=10.0, h=10.1, l=9.0, c=9.2)
    cur = C(o=9.5, h=9.9, l=9.4, c=9.8)  # no gap down at open
    assert piercing_line(prev, cur) is None


# --- tweezer bottom ---

def test_tweezer_bottom_matches():
    prev = C(o=10.0, h=10.1, l=9.0, c=9.2)
    cur = C(o=9.25, h=9.8, l=9.001, c=9.7)
    m = tweezer_bottom(prev, cur)
    assert m is not None


def test_tweezer_bottom_rejects_different_lows():
    prev = C(o=10.0, h=10.1, l=9.0, c=9.2)
    cur = C(o=9.25, h=9.8, l=9.5, c=9.7)  # low is well above prev low
    assert tweezer_bottom(prev, cur) is None


# --- morning star ---

def test_morning_star_matches():
    first = C(o=10.0, h=10.1, l=9.0, c=9.1)     # large bearish
    star = C(o=9.0, h=9.1, l=8.9, c=9.0)         # small body, gapped down
    third = C(o=9.1, h=10.0, l=9.05, c=9.9)      # bullish, closes deep into first's body
    m = morning_star(first, star, third)
    assert m is not None


def test_morning_star_rejects_shallow_third_close():
    first = C(o=10.0, h=10.1, l=9.0, c=9.1)
    star = C(o=9.0, h=9.1, l=8.9, c=9.0)
    third = C(o=9.1, h=9.4, l=9.05, c=9.3)  # doesn't close past first's midpoint (9.55)
    assert morning_star(first, star, third) is None


# --- three white soldiers ---

def test_three_white_soldiers_matches():
    a = C(o=9.0, h=9.55, l=8.95, c=9.5)
    b = C(o=9.2, h=10.05, l=9.15, c=10.0)
    c = C(o=9.6, h=10.55, l=9.55, c=10.5)
    m = three_white_soldiers(a, b, c)
    assert m is not None


def test_three_white_soldiers_rejects_descending_close():
    a = C(o=9.0, h=9.55, l=8.95, c=9.5)
    b = C(o=9.2, h=10.05, l=9.15, c=10.0)
    c = C(o=9.6, h=10.0, l=9.55, c=9.8)  # closes lower than b
    assert three_white_soldiers(a, b, c) is None


# --- resample + scan ---

def test_resample_aggregates_correctly():
    fine = [
        C(o=10.0, h=10.1, l=9.9, c=10.05, ts=0),
        C(o=10.05, h=10.2, l=10.0, c=10.1, ts=15),
        C(o=10.1, h=10.15, l=9.8, c=9.9, ts=30),
        C(o=9.9, h=10.0, l=9.85, c=9.95, ts=45),
    ]
    coarse = resample(fine, bucket_seconds=60)
    assert len(coarse) == 1
    assert coarse[0].open == 10.0
    assert coarse[0].close == 9.95
    assert coarse[0].high == 10.2
    assert coarse[0].low == 9.8


def test_scan_finds_engulfing_at_series_end():
    candles = [
        C(o=10.0, h=10.1, l=9.5, c=9.6, ts=0),
        C(o=9.55, h=10.3, l=9.5, c=10.2, ts=60),
    ]
    matches = scan(candles)
    names = {m.name for m in matches}
    assert "BULLISH_ENGULFING" in names


def test_scan_empty_input_returns_empty():
    assert scan([]) == []
