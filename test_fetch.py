from core.scraper import BSEScraper
from pathlib import Path
import sys
import asyncio
import logging
from datetime import datetime


def setup_global_logger(log_dir="logs"):
    """Sets up a single global logger for the entire script run."""
    run_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_log_dir = Path(log_dir) / run_timestamp
    run_log_dir.mkdir(parents=True, exist_ok=True)
    log_file = run_log_dir / "run.log"

    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Clear any existing handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create new handlers
    c_handler = logging.StreamHandler()
    f_handler = logging.FileHandler(log_file)
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    c_handler.setFormatter(formatter)
    f_handler.setFormatter(formatter)
    logger.addHandler(c_handler)
    logger.addHandler(f_handler)

    # Return the path for informational purposes
    return run_log_dir


async def main():
    """Main async function to run the test suite."""

    # Setup logger first to capture everything
    log_path = setup_global_logger()
    logging.info("🚀 --- KICKING OFF MANUAL-FILE VALIDATION TEST --- 🚀")
    logging.info(f"📝 Logging all output for this run to: {log_path}")

    assets_dir = Path("test_assets")
    required_files = {
        "text": assets_dir / "test_full_text.pdf",
        "web": assets_dir / "test_web_link.pdf",
        "video": assets_dir / "test_video_link.pdf",
        "audio": assets_dir / "test_audio_link.pdf",
    }

    for key, path in required_files.items():
        if not path.exists():
            logging.error(f"\n❌ FATAL ERROR: Test file not found at '{path}'")
            logging.error(
                "Please create this file in the 'test_assets' directory and try again."
            )
            sys.exit(1)

    logging.info("✅ All required test files found.")

    mock_announcements = {
        "1_web_link": {
            "NEWSID": "MANUAL_WEB_001",
            "SLONGNAME": "Your Web Corp",
            "PDF_URL_OVERRIDE": required_files["web"].absolute().as_uri(),
        },
        "2_video_link": {
            "NEWSID": "MANUAL_VIDEO_002",
            "SLONGNAME": "Your Media Corp (Video)",
            "PDF_URL_OVERRIDE": required_files["video"].absolute().as_uri(),
        },
        "3_audio_link": {
            "NEWSID": "MANUAL_AUDIO_003",
            "SLONGNAME": "Your Media Corp (Audio)",
            "PDF_URL_OVERRIDE": required_files["audio"].absolute().as_uri(),
        },
        "4_full_text": {
            "NEWSID": "MANUAL_TEXT_004",
            "SLONGNAME": "Your Transcript Corp",
            "PDF_URL_OVERRIDE": required_files["text"].absolute().as_uri(),
        },
    }

    # Create the scraper ONCE. It will use the global logger.
    scraper = BSEScraper(test_mode=False)
    all_notification_tasks = []

    for test_name, announcement in mock_announcements.items():
        announcement.setdefault("SCRIP_CD", "999999")
        announcement.setdefault("is_test", True)

        logging.info(f"\n\n--- RUNNING TEST: {test_name.upper()} ---")

        scraper.db.cursor.execute(
            "DELETE FROM announcements WHERE news_id = ?", (announcement["NEWSID"],)
        )
        scraper.db.conn.commit()
        logging.info(f"  -> Cleared database for {announcement['NEWSID']}.")

        # Run scraper and collect the notification tasks it produces
        tasks_from_run = scraper.run(announcements_override=[announcement])
        all_notification_tasks.extend(tasks_from_run)

    # Now, run all collected notification tasks sequentially at the end.
    if all_notification_tasks:
        await scraper.run_all_notifications_sequentially(all_notification_tasks)

    scraper.db.close()

    logging.info(
        "\n\n✅ --- ALL MANUAL TESTS FINISHED. Check logs and Telegram for results. --- ✅"
    )


if __name__ == "__main__":
    asyncio.run(main())
