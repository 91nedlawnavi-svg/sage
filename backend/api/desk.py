"""Promotion desk + impressions API — Wave 2, Blueprint §4 (drawer).

Elliot's approval surface for fact promotions, plus the read-only
impressions view. Same hygiene rules as graph.py: this layer never invents
ids — every id it serves comes straight from the store and is actionable
(trust suite property #4).

All handlers degrade to empty/ok — never 500 into the drawer.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from memory import relational_api as rel
from memory.sqlite_core import query
from utils.logger import log, warning

router = APIRouter()


@router.get("/api/desk/promotions")
async def get_promotions():
    """Pending fact nominations, with the impression + supporting episodes
    behind each so Elliot can judge with receipts."""
    try:
        out = []
        for p in rel.pending_promotions():
            item = {
                "queue_id": p["id"],
                "proposed_fact": p["proposed_fact"],
                "nominated_ts": p["nominated_ts"],
                "impression": None,
                "supporting_episodes": [],
            }
            if p["impression_id"]:
                imp = query("relational",
                    "SELECT * FROM impressions WHERE id=?", (p["impression_id"],))
                if imp:
                    item["impression"] = dict(imp[0])
                eps = query("relational",
                    "SELECT e.ts, e.content FROM episodes e "
                    "JOIN impression_support s ON s.episode_id=e.id "
                    "WHERE s.impression_id=? ORDER BY e.ts", (p["impression_id"],))
                item["supporting_episodes"] = [dict(r) for r in eps]
            out.append(item)
        return {"promotions": out}
    except Exception as exc:
        warning(f"desk/promotions: {type(exc).__name__}: {exc}")
        return {"promotions": []}


class Decision(BaseModel):
    queue_id: str
    approve: bool


@router.post("/api/desk/decide")
async def decide(d: Decision):
    """Elliot's call. Approve writes the fact (approved flag, provenance);
    reject closes the nomination. Idempotent on already-decided rows."""
    try:
        fid = await rel.decide_promotion(d.queue_id, approve=d.approve)
        log("desk", "decision", queue_id=d.queue_id, approve=d.approve,
            fact_id=fid)
        return {"ok": True, "fact_id": fid}
    except Exception as exc:
        warning(f"desk/decide: {type(exc).__name__}: {exc}")
        return {"ok": False, "fact_id": None}


@router.get("/api/desk/impressions")
async def get_impressions():
    """Drawer impressions view: active first, with support counts."""
    try:
        rows = query("relational",
            "SELECT * FROM impressions WHERE superseded_by IS NULL "
            "ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, ts_formed DESC "
            "LIMIT 100")
        return {"impressions": [dict(r) for r in rows]}
    except Exception as exc:
        warning(f"desk/impressions: {type(exc).__name__}: {exc}")
        return {"impressions": []}
