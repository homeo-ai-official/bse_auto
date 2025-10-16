# /home/pravin/Development/bse_scraper/test_single.py

from core.scraper import BSEScraper
import asyncio
import logging
from datetime import datetime
from pathlib import Path
import os


def setup_logging():
    """Configures the root logger for the application run."""
    run_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = Path("logs") / f"SINGLE_TEST-{run_timestamp}"
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
    # Quieten down the noisy google libraries
    logging.getLogger("google.api_core").setLevel(logging.WARNING)
    logging.getLogger("google.auth.transport.requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

    return log_dir


async def main():
    log_path = setup_logging()
    # use the root logger
    logger = logging.getLogger(__name__)

    logger.info("🧪 --- SINGLE PDF TEST ---")
    logger.info(f"Full logs for this run are in: {log_path}")

    url = os.getenv("SINGLE_TEST_PDF_URL")
    company = os.getenv("SINGLE_TEST_COMPANY_NAME", "Unknown")
    scrip = os.getenv("SINGLE_TEST_SCRIP_CODE", "000000")

    if not url:
        logger.error("❌ SINGLE_TEST_PDF_URL not set in .env")
        return

    mock = [
        {
            "NEWSID": "SINGLE_TEST_ID",
            "SLONGNAME": company,
            "SCRIP_CD": scrip,
            "PDF_URL_OVERRIDE": url,
        }
    ]

    scraper = BSEScraper(test_mode=False)

    tasks = await scraper.run(announcements_override=mock)
    if tasks:
        await scraper.run_all_notifications_sequentially(tasks)

    scraper.db.close()
    logger.info("✅ Single test complete.")


if __name__ == "__main__":
    asyncio.run(main())
