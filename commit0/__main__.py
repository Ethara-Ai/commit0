import logging
from commit0.cli import commit0_app

logger = logging.getLogger(__name__)


def main() -> None:
    """Main function to run the CLI"""
    logger.debug("Starting commit0 CLI")
    commit0_app()


if __name__ == "__main__":
    main()
