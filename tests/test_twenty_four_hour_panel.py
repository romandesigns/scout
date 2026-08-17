import time

from app.market import MarketWatcher, trading_session_key
from app.models import Bucket, SymbolState


class DummyStore:
    def latest_findings_by_ticker(self, tickers):
        return {}


def _watcher():
    watcher = object.__new__(MarketWatcher)
    watcher.states = {}
    watcher.store = DummyStore()
    return watcher


def _state(symbol, now, boats=True):
    s = SymbolState(symbol, 15, 120)
    s.session_date = trading_session_key(now)
    s.current = Bucket(now - (now % 15), 5.0, 5.1, 4.9, 5.05, 10000, 20)
    s.session_first_price = 5.0
    s.session_volume = 100000
    s.session_pv = 500000
    s.price_points.extend([(now-30, 4.95),(now-15,5.0),(now,5.05)])
    s.last_market_trade_at = now
    s.last_market_feed = 'boats' if boats else 'sip'
    if boats:
        s.last_boats_trade_at = now
        s.boats_session_date = s.session_date
    return s


def test_24h_panel_only_includes_boats_verified(monkeypatch):
    now=time.time()
    watcher=_watcher()
    watcher.states={'BOAT':_state('BOAT',now,True),'SIPX':_state('SIPX',now,False)}
    monkeypatch.setattr(watcher, '_metrics', lambda state, ts: {
        'actionable_rank':'A','quality_label':'CLEAN','quality_score':100,'ross_match':False,'ross_score':0,
        'change5':0.2,'change15':0.3,'change30':0.4,'vol15':5.0,'dollar15':10000,'trades15':20,
        'extension':0.4,'trigger_distance_pct':0.1,'rejection_reasons':[]})
    rows=watcher.twenty_four_hour_rows(50)
    assert [r['ticker'] for r in rows] == ['BOAT']
    assert rows[0]['verified_24h'] is True


def test_24h_panel_uses_shared_current_quality_metrics(monkeypatch):
    now=time.time(); watcher=_watcher(); watcher.states={'BOAT':_state('BOAT',now,True)}
    monkeypatch.setattr(watcher, '_metrics', lambda state, ts: {
        'actionable_rank':'B','quality_label':'CLEAN','quality_score':82,'ross_match':True,'ross_score':88,
        'change5':0.35,'change15':0.3,'change30':0.25,'vol15':7.0,'dollar15':22000,'trades15':31,
        'extension':0.5,'trigger_distance_pct':-0.1,'rejection_reasons':[]})
    row=watcher.twenty_four_hour_rows(1)[0]
    assert row['actionable_rank']=='B'
    assert row['quality_score']==82
    assert row['ross_match'] is True
    assert row['change_5s_pct']==0.35
