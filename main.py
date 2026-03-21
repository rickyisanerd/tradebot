from tradebot.cli import main as cli_main
from tradebot.dashboard import create_app

app = create_app()

if __name__ == "__main__":
    raise SystemExit(cli_main())
