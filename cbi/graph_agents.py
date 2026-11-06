"""LangGraph orchestration for scheduled public-data collection.

Daily runs rotate news and governance collection across city banks (plus an
industry digest); weekly runs refresh financial statements. Bank-level work is
fanned out with ``Send`` and merged back into the JSON stores by a single
persistence node, so agent files are never written concurrently.
"""
from __future__ import annotations

import argparse
import json
import operator
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

import config
from cbi import build as build_mod
from cbi import financial_agent, governance_agent, news_agent, nfra_penalty, registry

STATE_DIR = config.DATA_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINT_PATH = STATE_DIR / "checkpoints.sqlite"
RUN_LOG = STATE_DIR / "runs.json"


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunState(TypedDict, total=False):
    mode: str
    run_id: str
    limit: int
    group: str
    days: int
    use_llm: bool
    force: bool
    build: bool
    max_concurrency: int
    max_tasks: int
    penalties: int
    tasks: list[dict]
    results: Annotated[list[dict], operator.add]
    failures: Annotated[list[dict], operator.add]
    stats: dict


def _task(kind: str, bank: dict | None = None, **extra) -> dict:
    row = {"kind": kind}
    if bank:
        row.update({"bank_id": bank["id"], "bank": bank["short_name"]})
    row.update(extra)
    return row


def prepare(state: RunState) -> dict:
    mode = state.get("mode", "daily")
    limit = int(state.get("limit") or 20)
    force = bool(state.get("force"))
    tasks: list[dict] = []
    days = int(state.get("days") or 180)
    use_llm = bool(state.get("use_llm", True))
    task_opts = {"force": force, "use_llm": use_llm, "days": days}

    if mode in ("daily", "bootstrap", "all"):
        for bank in news_agent.select_banks(limit, force=force):
            tasks.append(_task("news", bank, **task_opts))
        gov_limit = limit if mode == "bootstrap" else max(1, limit // 2)
        for bank in governance_agent.select_banks(gov_limit, force=force):
            tasks.append(_task("governance", bank, **task_opts))
        tasks.append(_task("industry"))
        if state.get("penalties"):
            tasks.append(_task("penalties", pages=int(state["penalties"])))

    if mode in ("weekly", "bootstrap", "all"):
        for bank in financial_agent.select_banks(limit, group=state.get("group", "city"),
                                                 force=force):
            tasks.append(_task("financial", bank, **task_opts))

    budget = int(state.get("max_tasks") or 80)
    truncated = len(tasks) > budget
    if truncated:
        tasks = tasks[:budget]
    return {"tasks": tasks,
            "stats": {"tasks_planned": len(tasks), "budget_truncated": truncated}}


def dispatch(state: RunState):
    sends = [Send("process_task", task) for task in state.get("tasks") or []]
    return sends or "persist"


def process_task(task: dict) -> dict:
    kind = task["kind"]
    try:
        if kind == "industry":
            payload = news_agent.collect_industry()
            return {"results": [
                {"kind": "industry", "items": len(payload.get("items") or []), "at": _now()}
            ]}
        if kind == "penalties":
            summary = nfra_penalty.crawl(max_pages=int(task.get("pages") or 0),
                                         verbose=False)
            return {"results": [{"kind": "penalties", "summary": summary, "at": _now()}]}

        bank = registry.load_registry()["banks"]
        bank = next(b for b in bank if b["id"] == task["bank_id"])

        if kind == "news":
            items = news_agent.fetch_bank_news(
                bank, days=int(task.get("days") or 180),
                use_cache=not task.get("force", False),
                use_llm=task.get("use_llm", True))
            return {"results": [{"kind": "news", "bank_id": bank["id"],
                                 "short_name": bank["short_name"], "items": items,
                                 "at": _now()}]}

        if kind == "governance":
            item = governance_agent.fetch_bank(bank, use_cache=not task.get("force", False))
            return {"results": [{"kind": "governance", "bank_id": bank["id"],
                                 "short_name": bank["short_name"], "item": item,
                                 "at": _now()}]}

        if kind == "financial":
            result = None
            if (financial_agent.A_CODES.get(bank.get("short_name") or "")
                    or financial_agent.A_CODES.get(bank.get("name") or "")):
                try:
                    result = financial_agent.fetch_listed(bank)
                except Exception:
                    result = None
            if not result:
                result = financial_agent.fetch_unlisted(
                    bank, use_cache=not task.get("force", False),
                    use_llm=task.get("use_llm", True))
            return {"results": [{"kind": "financial", "bank_id": bank["id"],
                                 "short_name": bank["short_name"], "item": result,
                                 "force": task.get("force", False), "at": _now()}]}

        return {"failures": [{"kind": kind, "error": "unknown task", "at": _now()}]}
    except Exception as exc:  # keep one bad bank from failing the batch
        return {"failures": [{"kind": kind, "bank_id": task.get("bank_id"),
                              "error": type(exc).__name__, "at": _now()}]}


def persist(state: RunState) -> dict:
    results = state.get("results") or []
    banks = registry.load_registry()["banks"]
    by_id = {b["id"]: b for b in banks}
    counts = {"news": 0, "governance": 0, "financial": 0}

    news_rows = [r for r in results if r["kind"] == "news"]
    if news_rows:
        by_bank = _load_json(news_agent.BY_BANK_PATH, {})
        for row in news_rows:
            if row.get("items"):
                by_bank[row["bank_id"]] = row["items"]
                counts["news"] += 1
        _save_json(news_agent.BY_BANK_PATH, by_bank)
        _save_json(news_agent.COVERAGE_PATH, {
            "generated_at": _now(),
            "banks_checked": len(news_rows),
            "banks_with_news": sum(1 for r in news_rows if r.get("items")),
            "news_items": sum(len(r.get("items") or []) for r in news_rows),
            "days": state.get("days") or 180,
            "llm": bool(state.get("use_llm", True)),
            "orchestrator": "langgraph",
        })

    gov_rows = [r for r in results if r["kind"] == "governance" and r.get("item")]
    if gov_rows:
        data = _load_json(governance_agent.OUT_PATH, {})
        for row in gov_rows:
            data[row["bank_id"]] = row["item"]
            counts["governance"] += 1
        data, overlaps = governance_agent.build_links(data, banks)
        _save_json(governance_agent.OUT_PATH, data)
        _save_json(governance_agent.OVERLAP_PATH, {
            "generated_at": _now(),
            "candidates": overlaps[:200],
            "note": "同名董监高仅作人工核验线索，未写入关系图。",
        })
        _save_json(governance_agent.COVERAGE_PATH, {
            "generated_at": _now(),
            "banks_checked": len(gov_rows),
            "banks_with_people": sum(1 for r in gov_rows if r.get("item")),
            "total_banks_with_people": sum(1 for v in data.values() if v.get("people")),
            "people_total": sum(len(v.get("people") or []) for v in data.values()),
            "explicit_links": sum(len(v.get("shares") or []) for v in data.values()),
            "same_name_overlap_groups": len(overlaps),
            "orchestrator": "langgraph",
        })

    fin_rows = [r for r in results if r["kind"] == "financial" and r.get("item")]
    if fin_rows:
        data = _load_json(financial_agent.OUT_PATH, {})
        for row in fin_rows:
            previous = data.get(row["bank_id"]) or {}
            data[row["bank_id"]] = row["item"] if row.get("force") else {
                **previous, **{k: v for k, v in row["item"].items() if v is not None}
            }
            counts["financial"] += 1
        _save_json(financial_agent.OUT_PATH, data)
        _save_json(financial_agent.COVERAGE_PATH, {
            "generated_at": _now(),
            "group": state.get("group", "city"),
            "banks_checked": len(fin_rows),
            "banks_with_data": len(fin_rows),
            "total_banks_with_data": len(data),
            "orchestrator": "langgraph",
        })

    stats = {**(state.get("stats") or {}), "persisted": counts,
             "failures": len(state.get("failures") or []),
             "finished_at": _now()}
    _append_run(state, stats)
    return {"stats": stats}


def _append_run(state: RunState, stats: dict) -> None:
    log = _load_json(RUN_LOG, {"runs": []})
    log.setdefault("runs", []).append({
        "run_id": state.get("run_id"),
        "mode": state.get("mode"),
        "stats": stats,
    })
    log["runs"] = log["runs"][-90:]
    _save_json(RUN_LOG, log)


def build_node(state: RunState) -> dict:
    if not state.get("build", True):
        return {"stats": {**(state.get("stats") or {}), "built": False}}
    manifest = build_mod.build()
    validation = build_mod.validate()
    stats = {**(state.get("stats") or {}),
             "built": True,
             "build_counts": manifest.get("counts"),
             "validation_ok": not any(validation.get(k) for k in
                                      ("duplicate_ids", "dangling", "missing_details"))
             if isinstance(validation, dict) else None}
    return {"stats": stats}


def route_after_persist(state: RunState) -> str:
    return "build" if state.get("build", True) else END


def build_graph(checkpointer=None):
    graph = StateGraph(RunState)
    graph.add_node("prepare", prepare)
    graph.add_node("process_task", process_task,
                   retry_policy=RetryPolicy(max_attempts=2, initial_interval=2,
                                            backoff_factor=2))
    graph.add_node("persist", persist)
    graph.add_node("build", build_node)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", dispatch, ["process_task", "persist"])
    graph.add_edge("process_task", "persist")
    graph.add_conditional_edges("persist", route_after_persist, ["build", END])
    graph.add_edge("build", END)
    return graph.compile(checkpointer=checkpointer)


def run(mode: str = "daily", limit: int = 20, group: str = "city", days: int = 180,
        use_llm: bool = True, force: bool = False, build: bool = True,
        max_concurrency: int = 4, penalties: int = 0,
        run_id: str | None = None) -> dict:
    """Execute one orchestrated run and return its final state."""
    run_id = run_id or f"{mode}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    tasks_force = force
    state: RunState = {
        "mode": mode, "run_id": run_id, "limit": limit, "group": group,
        "days": days, "use_llm": use_llm, "force": tasks_force, "build": build,
        "max_concurrency": max_concurrency, "penalties": penalties,
        "results": [], "failures": [], "stats": {},
    }
    with SqliteSaver.from_conn_string(str(CHECKPOINT_PATH)) as saver:
        graph = build_graph(saver)
        final = graph.invoke(
            state,
            config={"configurable": {"thread_id": run_id},
                    "max_concurrency": max_concurrency},
        )
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description="LangGraph data-collection orchestrator")
    parser.add_argument("--mode", default="daily",
                        choices=["daily", "weekly", "bootstrap", "all"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--group", default="city", choices=["city", "rural", "village", "all"])
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--penalties", type=int, default=0,
                        help="max NFRA list pages to crawl in this run")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    final = run(mode=args.mode, limit=args.limit, group=args.group, days=args.days,
                use_llm=not args.no_llm, force=args.force, build=not args.no_build,
                max_concurrency=args.concurrency, penalties=args.penalties)
    print(json.dumps(final.get("stats") or {}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
