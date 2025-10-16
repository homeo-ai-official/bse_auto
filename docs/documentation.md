
---

# BSE India AI-Powered Announcement Scraper

## 1. Overview

This project is an automated, real-time scraper designed to monitor the Bombay Stock Exchange (BSE) India website for "Earnings Call Transcript" announcements. It goes beyond simple data fetching by employing a sophisticated pipeline to download associated PDF documents, analyze their content, generate AI-powered summaries using Google's Gemini API, and deliver actionable insights and notifications via Telegram.

The system is built to run continuously, operate in historical "backfill" mode, or test single documents, making it a versatile tool for financial analysis and data collection.

## 2. Key Features

- **Real-Time Polling:** Continuously monitors the BSE API for new announcements at a configurable interval.
- **Historical Backfilling:** Can be configured to process all announcements within a specific date range.
- **AI-Powered Summarization:** Leverages the Google Gemini API to generate concise, investment-focused summaries of lengthy transcripts.
- **Intelligent Content Processing:** Differentiates between PDFs containing full transcripts and those that are merely pointers to external media links (audio/video).
- **Multi-Stage Analysis:** If a PDF points to an external media file, the system downloads that file and submits it to the Gemini API for direct summarization.
- **Dual-Channel Telegram Notifications:**
    - **Summaries Channel:** Sends polished AI summaries to a designated user (e.g., an analyst or manager).
    - **Links & Errors Channel:** Sends notifications about web links found, processing errors, or media links to a technical user (e.g., a developer) for monitoring.
- **Persistent State:** Uses a SQLite database to track processed announcements, preventing duplicate work and allowing the scraper to be stopped and restarted safely.
- **Robust & Resilient:** Implements retry logic with exponential backoff for all critical network operations (API calls, downloads, AI queries, notifications).
- **Comprehensive Logging:** Creates detailed, timestamped log files for each run, separating live, backfill, and test runs for easy debugging.
- **Modular Architecture:** Clean separation of concerns into distinct modules for scraping, database handling, PDF processing, AI summarization, and notifications.

## 3. System Architecture & Data Flow

The application follows a clear, sequential data processing pipeline:

1.  **Initiation:** An entry-point script (`main.py`, `backfill.py`, or `test_single.py`) starts the process. It sets up global logging for the run.
2.  **Fetching:** The `BSEScraper` queries the BSE API to fetch a list of recent "Earnings Call Transcript" announcements.
3.  **Filtering:** The scraper iterates through the announcements, checking each `NEWSID` against the local SQLite database (`DBHandler`) to filter out items that have already been fully processed.
4.  **URL Discovery:** For each new item, the scraper makes a secondary request to an XBRL endpoint to discover the direct URL of the associated PDF document.
5.  **Downloading:** The PDF is downloaded and saved locally to a `downloads/` directory. Its status is recorded in the database as `DOWNLOADED`.
6.  **Processing (`PDFProcessor`):** The core business logic is applied here:
    - The PDF's page count is checked.
    - **If > 3 pages:** The document is considered a **full transcript**. Its text content is extracted.
    - **If <= 3 pages:** The document is considered a **link pointer**. The text is scanned for URLs.
7.  **Summarization (`GeminiSummarizer`):**
    - **For full transcripts:** The extracted text is sent to the Gemini API with a detailed prompt to generate a summary and sentiment analysis.
    - **For link pointers:**
        - If a **media link** (e.g., `.mp4`, `.mp3`) is found, the media file is downloaded, uploaded to the Gemini API, and summarized directly.
        - If a **web link** is found, it is recorded for notification without a summary.
        - If an **error** occurs (e.g., no actionable content), an error object is created.
8.  **Database Update:** The result from the summarizer (summary JSON, link data, or error info) is saved to the SQLite database, and the item's status is updated to `PROCESSED` or `ERROR_PROCESSING`.
9.  **Notification Task Queuing:** A callable "task factory" for the appropriate notification (summary, link, or error) is created and added to a list. This defers the actual sending of notifications until all scraping and processing is complete.
10. **Sequential Notification (`TelegramNotifier`):** After the main loop finishes, the system iterates through the queued notification tasks, executing them one by one with a delay to avoid rate-limiting and ensure messages are sent in chronological order.

## 4. Codebase Breakdown

### 4.1. Entry Points

-   `main.py`: The primary entry point for **live, continuous operation**. It sets up logging and runs the scraper in an infinite loop with a configurable polling interval.
-   `backfill.py`: The entry point for **historical data processing**. It reads `START_DATE` and `END_DATE` from the `.env` file and performs a single, comprehensive run.
-   `test_single.py`: A utility script for **debugging and testing**. It processes a single PDF URL defined in the `.env` file, allowing for rapid testing of the entire processing and notification pipeline.

### 4.2. Core Modules (`core/`)

-   **`scraper.py` (`BSEScraper` class):**
    -   The central orchestrator of the application.
    -   Manages configuration, makes API requests to BSE, and coordinates the entire workflow from fetching announcements to queuing notifications.
    -   Handles the logic for downloading PDFs and interacting with all other core modules.
    -   `run()`: The main asynchronous method that executes a single scrape-and-process cycle.
    -   `run_all_notifications_sequentially()`: Ensures notifications are sent gracefully at the end of a run.

-   **`db_handler.py` (`DBHandler` class):**
    -   Manages all interactions with the `database.db` SQLite database.
    -   Creates the `announcements` table if it doesn't exist.
    -   Provides methods to check if an announcement has been processed (`is_processed`), add new announcements (`add_new_announcement`), and update records with summary data (`update_summary`).
    -   Ensures data persistence and state management.

-   **`processor.py` (`PDFProcessor` class):**
    -   Contains the critical business logic for analyzing downloaded PDFs.
    -   Its main method, `process_pdf()`, determines whether a PDF is a content-rich transcript or a simple link pointer based on its page count.
    -   Extracts full text or finds and classifies URLs within the PDF.

-   **`summarizer.py` (`GeminiSummarizer` class):**
    -   Interfaces with the Google Gemini Pro API.
    -   Contains sophisticated prompts engineered for high-quality financial analysis.
    -   `summarize()`: The main orchestration method that takes processed data and decides whether to perform text summarization, initiate media summarization, or format a link notification.
    -   `_summarize_media_from_url()`: Handles the complex process of downloading an external media file, uploading it to Gemini, and generating a summary from it.

-   **`notifier.py` (`TelegramNotifier` class):**
    -   Handles all communication with the Telegram Bot API.
    -   Formats and sends messages to two different chat IDs as configured in the `.env` file.
    -   `notify_summary()`: Sends the AI-generated summary.
    -   `notify_weblink()`: Sends notifications about found web links.
    -   `notify_error()`: Sends detailed error messages for failed processing attempts.
    -   Includes robust retry logic and Markdown V2 escaping to ensure reliable message delivery.

## 5. Setup and Installation

### 5.1. Prerequisites
-   sudo apt update && sudo apt upgrade -y
-   Python 3.8+
-   `pip` package installer

### 5.2. Installation Steps

1.  **Clone the Repository:**
    ```bash
    git clone (https://github.com/homeo-ai-official/bse_auto.git)
    cd bse_scraper
    ```

2.  **Create a Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install Dependencies:**
    A `requirements.txt` file should be created with the project's dependencies.
    ```bash
    pip install -r requirements.txt
    ```
    *Key dependencies include `requests`, `lxml`, `PyPDF2`, `python-dotenv`, `google-generativeai`, `python-telegram-bot`.*

4.  **Configure Environment Variables:**
    Copy the provided `.env` file or create a new one named `.env` in the project root. Fill in the required values:

    ```ini
    # .env

    # --- Scraper Configuration ---
    # Used by main.py for real-time polling (in hours).
    LOOKBACK_HOURS=24

    # --- FOR BACKFILLING ONLY (used by backfill.py) ---
    # Format: YYYYMMDD
    START_DATE=20250101
    END_DATE=20250131
    # Limit the number of *new* items processed in a backfill run. 0 = unlimited.
    MAX_ITEMS_TO_PROCESS=50

    # --- Gemini API Configuration ---
    # Get this from Google AI Studio.
    GEMINI_API_KEY="YOUR_GEMINI_API_KEY"

    # --- Telegram Notifications ---
    # Get this from BotFather on Telegram.
    TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
    # The numeric chat ID for the channel/user who receives summaries.
    TELEGRAM_CHAT_ID_SUMMARIES="CHAT_ID_FOR_SUMMARIES"
    # The numeric chat ID for the channel/user who receives links and errors.
    TELEGRAM_CHAT_ID_LINKS="CHAT_ID_FOR_LINKS_AND_ERRORS"

    # --- Single PDF Test (used by test_single.py) ---
    SINGLE_TEST_PDF_URL="https://www.bseindia.com/..../some-pdf-url.pdf"
    SINGLE_TEST_COMPANY_NAME="Name of The Company"
    SINGLE_TEST_SCRIP_CODE="123456"
    ```

## 6. Usage

Ensure your virtual environment is activated before running any commands.

### 6.1. Live Polling Mode

To start the scraper in its continuous, real-time monitoring mode:

```bash
python main.py
```

The scraper will run indefinitely, polling for new announcements every 60 seconds (as configured in `main.py`). Press `Ctrl+C` to stop it gracefully.

### 6.2. Historical Backfill Mode

To run the scraper for a specific date range:

1.  Set the `START_DATE` and `END_DATE` variables in your `.env` file.
2.  Optionally, set `MAX_ITEMS_TO_PROCESS` to limit the scope of the run.
3.  Execute the backfill script:

```bash
python backfill.py
```

The script will process all announcements within the date range and then exit.

### 6.3. Single PDF Test Mode

To test the entire pipeline with a single, known PDF:

1.  Set the `SINGLE_TEST_PDF_URL`, `SINGLE_TEST_COMPANY_NAME`, and `SINGLE_TEST_SCRIP_CODE` variables in your `.env` file.
2.  Execute the test script:

```bash
python test_single.py
```

This will download, process, summarize, and send a notification for that single document, providing a quick way to verify that all systems are working correctly.

## 7. Database Schema

The application uses a single SQLite database file (`database.db`) with one table: `announcements`.

| Column               | Type       |   Description                                                                   |
| -------------------- | --------   | ---------------------------------------------------------------------------     |
| `news_id`            | `TEXT`     |   **Primary Key**. The unique identifier for the announcement from BSE.         |
| `scrip_code`         | `TEXT`     |   The company's stock ticker code.                                              |
| `company_name`       | `TEXT`     |   The full name of the company.                                                 |
| `download_timestamp` | `DATETIME` |   Timestamp when the announcement was first added to the database.              |
| `status`             | `TEXT`     |   The processing status. Can be `DOWNLOADED`, `PROCESSED`, `ERROR_PROCESSING`.  |
| `summary_json`       | `TEXT`     |   A JSON string containing the final output from the `GeminiSummarizer`.        |

## 8. Logging

Logs are critical for monitoring and debugging. The system creates a `logs/` directory with a unique sub-directory for each run, prefixed with the run type (`LIVE`, `BACKFILL`, `SINGLE_TEST`) and a timestamp.

-   **Example Log Path:** `logs/LIVE-20240725-103000/run.log`
-   Each log file contains detailed, timestamped information about every step of the process, including API requests, file downloads, processing decisions, and notification attempts.
-   The verbosity of third-party libraries like `google-api-core` is reduced to keep the logs clean and focused on the application's own operations.