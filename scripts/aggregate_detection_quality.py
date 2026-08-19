#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, math, statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

def f(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None

def main():
    p=argparse.ArgumentParser(description="Aggregate Scout detection-quality JSON reports across sessions/versions")
    p.add_argument("--glob", default="detection-quality-*.json")
    p.add_argument("--output", default="data/optimization/detection-quality-trend.json")
    a=p.parse_args()
    files=sorted(glob.glob(a.glob))
    rows=[]
    reports=[]
    for name in files:
        try: data=json.loads(Path(name).read_text(encoding="utf-8"))
        except Exception: continue
        summary=data.get("summary",{})
        version=str(summary.get("engine_version") or "UNKNOWN")
        r=[x for x in data.get("rows",[]) if isinstance(x,dict)]
        for x in r:
            y=dict(x); y["_source"]=name; y["_engine_version"]=version; rows.append(y)
        reports.append({"file":name,"engine_version":version,"selected_count":summary.get("selected_count"),"rows":len(r)})
    by_version=defaultdict(list)
    for r in rows: by_version[r["_engine_version"]].append(r)
    versions={}
    for version,rs in sorted(by_version.items()):
        def vals(key): return [v for v in (f(x.get(key)) for x in rs) if v is not None]
        ret5=vals("return_300s_pct"); ret15=vals("return_900s_pct"); mfe=vals("mfe_300s_pct"); mae=vals("mae_300s_pct")
        classes=Counter(str(x.get("classification") or "UNKNOWN") for x in rs)
        stages=Counter(str(x.get("stage") or "UNKNOWN") for x in rs)
        versions[version]={
            "rows":len(rs),"classes":dict(classes),"stages":dict(stages),
            "avg_5m_pct":statistics.mean(ret5) if ret5 else None,
            "avg_15m_pct":statistics.mean(ret15) if ret15 else None,
            "avg_mfe_5m_pct":statistics.mean(mfe) if mfe else None,
            "avg_mae_5m_pct":statistics.mean(mae) if mae else None,
            "positive_5m_rate":sum(x>0 for x in ret5)/len(ret5) if ret5 else None,
            "positive_15m_rate":sum(x>0 for x in ret15)/len(ret15) if ret15 else None,
        }
    out={"reports":reports,"versions":versions,"total_rows":len(rows)}
    path=Path(a.output); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps(out,indent=2))
    print(f"Trend report: {path.resolve()}")
if __name__=="__main__": main()
