"""Run the single-process service behind nginx, never on a public interface."""

import sys

from .config import ConfigurationError, Settings


def main() -> int:
    try:
        settings = Settings.from_env()
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    try:
        import uvicorn
        from .app import create_app
    except ModuleNotFoundError:
        print("Install the server extra to run Connect the Bots.", file=sys.stderr)
        return 2
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=8100,
        workers=1,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        server_header=False,
        log_level="warning",
        timeout_keep_alive=5,
        limit_concurrency=100,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
