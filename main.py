from tradebot.cli import main
from tradebot.dashboard import app  # noqa: F401 – exposed for ASGI servers (e.g. `uvicorn main:app`)

if __name__ == "__main__":
    raise SystemExit(main())
