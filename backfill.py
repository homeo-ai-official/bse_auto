# /home/pravin/Development/bse_scraper/backfill.py 

from core.scraper import BSEScraper
import os
import asyncio
import logging
from datetime import datetime
from pathlib import Path


def setup_logging():
    """Configures the root logger for the application run."""
    run_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # Create a specific log directory for this backfill run
    log_dir = Path("logs") / f"BACKFILL-{run_timestamp}"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # --- IMPORTANT: Clear any existing handlers ---

    if logger.hasHandlers():
        logger.handlers.clear()

    # Create handlers
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Console handler
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # File handler
    file_handler = logging.FileHandler(log_dir / "run.log")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # --- Control third-party library verbosity ---
    # Quieten down noisy libraries to keep our logs clean
    logging.getLogger("google.api_core").setLevel(logging.WARNING)
    logging.getLogger("google.auth.transport.requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

    return log_dir


async def main():
    """Main async function to run the backfill process."""
    log_path = setup_logging()

    logger = logging.getLogger(__name__)

    start_date = os.getenv("START_DATE")
    end_date = os.getenv("END_DATE")

    if not start_date or not end_date:
        logger.error(
            "❌ ERROR: START_DATE and END_DATE must be set in the .env file for a backfill run."
        )
        return

    logger.info(
        f"🚀 --- Running a REAL data backfill from {start_date} to {end_date} --- 🚀"
    )
    logger.info(f"📝 Full logs for this run are in: {log_path}")

    scraper = BSEScraper(test_mode=False)

    # Run the scraper and collect all notification tasks
    notification_tasks = await scraper.run()

    # At the end of the entire run, send all collected notifications sequentially
    if notification_tasks:
        await scraper.run_all_notifications_sequentially(notification_tasks)

    scraper.db.close()
    logger.info("✅ --- Backfill run complete. --- ✅")


if __name__ == "__main__":
    asyncio.run(main())
