"""`foodwise` command line: api, worker, migrate, seed, train, benchmark, openapi."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def _migrate(url: str | None = None) -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    if url:
        cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")


def main(argv: list[str] | None = None) -> None:
    from foodwise.config import get_settings
    from foodwise.core.logging import configure_logging

    parser = argparse.ArgumentParser(prog="foodwise")
    sub = parser.add_subparsers(dest="cmd", required=True)
    api = sub.add_parser("api", help="run the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--reload", action="store_true")
    sub.add_parser("worker", help="run the job worker and scheduler")
    sub.add_parser("migrate", help="apply database migrations")
    seed = sub.add_parser("seed", help="load the demo college (development only)")
    seed.add_argument("--reset", action="store_true", help="delete all data first")
    seed.add_argument("--if-empty", action="store_true", help="only seed an empty database")
    sub.add_parser("demo-tick", help="continue the demo college up to today")
    train = sub.add_parser("train", help="train models for every canteen now")
    train.add_argument("--tenant", help="tenant slug (default: all)")
    bench = sub.add_parser("benchmark", help="clean the Genpact food demand dataset and backtest the models on it")
    bench.add_argument("--data-dir", help="folder with train.csv, meal_info.csv and fulfilment_center_info.csv")
    out = sub.add_parser("openapi", help="write the OpenAPI document")
    out.add_argument("path", nargs="?", default="-")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    log = logging.getLogger("foodwise.cli")

    if args.cmd == "api":
        import uvicorn

        uvicorn.run("foodwise.main:app", host=args.host, port=args.port, reload=args.reload, log_config=None)
    elif args.cmd == "worker":
        from foodwise.worker import run

        run()
    elif args.cmd == "migrate":
        _migrate()
        log.info("migrations applied")
    elif args.cmd == "seed":
        from foodwise.sim.seed import is_empty, seed_demo

        if args.if_empty and not is_empty():
            log.info("database already has data; skipping seed")
            return
        result = seed_demo(reset=args.reset)
        log.info("seeded: %s", json.dumps(result, default=str))
    elif args.cmd == "demo-tick":
        from sqlalchemy import select

        from foodwise.db.base import system_session
        from foodwise.db.models import Tenant
        from foodwise.sim.live import advance_demo

        with system_session() as db:
            ids = [t.id for t in db.execute(select(Tenant).where(Tenant.is_demo.is_(True))).scalars()]
        for tid in ids:
            log.info("demo %s: %s", tid, json.dumps(advance_demo(tid), default=str))
    elif args.cmd == "train":
        from sqlalchemy import select

        from foodwise.db.base import system_session
        from foodwise.db.models import Canteen, Tenant
        from foodwise.ml.training import train_canteen

        with system_session() as db:
            q = select(Canteen.tenant_id, Canteen.id, Canteen.name).join(Tenant, Tenant.id == Canteen.tenant_id)
            if args.tenant:
                q = q.where(Tenant.slug == args.tenant)
            targets = db.execute(q).all()
        for tenant_id, canteen_id, name in targets:
            result = train_canteen(tenant_id, canteen_id, trigger="cli")
            log.info("%s: %s", name, json.dumps(result, default=str))
    elif args.cmd == "benchmark":
        from foodwise.benchmark import genpact

        data_dir = Path(args.data_dir) if args.data_dir else settings.genpact_dir
        try:
            report, df = genpact.run(data_dir, progress=lambda label, frac: log.info("%3.0f%% %s", frac * 100, label))
        except genpact.BenchmarkError as exc:
            sys.exit(str(exc))
        genpact.save(report, df)
        for r in report["leaderboard"]:
            log.info("%-36s WAPE %5.1f%%  RMSLE %.3f  %s", r["label"], r["wape"] * 100, r["rmsle"], r["status"])
        log.info("report saved; open Learn > Real-data benchmark in the app")
    elif args.cmd == "openapi":
        from foodwise.main import create_app

        doc = json.dumps(create_app().openapi(), indent=2)
        if args.path == "-":
            sys.stdout.write(doc)
        else:
            Path(args.path).write_text(doc, encoding="utf-8")


if __name__ == "__main__":
    main()
