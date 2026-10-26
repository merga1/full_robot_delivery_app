import asyncio
import logging

from .config import load_config
from .gateway import build_gateway


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    asyncio.run(build_gateway(config).run_forever())


if __name__ == "__main__":
    main()
