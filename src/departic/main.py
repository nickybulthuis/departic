import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from departic import __version__, scheduler
from departic.config import Settings
from departic.routing import load_cache, precache
from departic.web.router import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Departic starting...")
    load_cache()

    cfg = Settings.get()
    if cfg and cfg.evcc.home_address:
        precache([cfg.evcc.home_address])

    scheduler.start()
    yield
    scheduler.stop()

    log.info("Departic stopped.")


app = FastAPI(
    title="Departic",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# Mount static files first, then include router
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(router)


def run() -> None:
    uvicorn.run(
        "departic.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8080,
        log_level="info",
    )


if __name__ == "__main__":
    run()
