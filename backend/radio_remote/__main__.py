import argparse
import logging
import os
import signal
import sys

from aiohttp import web

from . import config
from .app import create_app


def _restart_hook():
    """Ask the process to exit cleanly (SIGTERM => aiohttp shuts down, TxGuard un-keys); systemd then restarts it."""
    import threading
    threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()


def main() -> None:
    ap = argparse.ArgumentParser(prog="radio_remote")
    ap.add_argument("--config", default="config/radio-remote.toml")
    args = ap.parse_args()
    cfg = config.load(args.config)
    logging.basicConfig(
        level=cfg["logging"]["level"].upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("startup")
    log.info("radio.model=%s allow_ptt=%s", cfg["radio"]["model"], cfg["safety"]["allow_ptt"])
    app = create_app(cfg, config_path=args.config,
                     restart_hook=_restart_hook if sys.platform != "win32" else None)
    web.run_app(app, host=cfg["server"]["host"], port=cfg["server"]["port"], print=None, access_log=None)


if __name__ == "__main__":
    main()
