# core/scraper.py
import requests
from lxml import etree
from datetime import datetime, timedelta
from pathlib import Path
import time
import os
from dotenv import load_dotenv
import json
import logging
import asyncio
from urllib.parse import urlparse
from typing import Callable, Awaitable

from .db_handler import DBHandler
from .processor import PDFProcessor
from .summarizer import GeminiSummarizer
from .notifier import TelegramNotifier

load_dotenv()


class BSEScraper:
    def __init__(self, test_mode=False):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Referer": "https://www.bseindia.com/",
            "Origin": "https://www.bseindia.com",
        }
        self.api_url = (
            "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
        )
        self.xbrl_base_url = "https://www.bseindia.com/Msource/90D/CorpXbrlGen.aspx"
        self.download_path = Path("downloads")
        self.download_path.mkdir(exist_ok=True)
        self.test_mode = test_mode

        # Get the logger that was configured by the entry-point script.
        self.logger = logging.getLogger(__name__)

        # Define the path for the URL log file used in test_mode.
        log_dir_path = Path("logs")
        log_dir_path.mkdir(exist_ok=True)
        self.url_log_file = log_dir_path / "pdf_urls.log"

        # --- DATABASE & PROCESSORS ---
        self.db = DBHandler()
        self.pdf_processor = PDFProcessor()
        self.summarizer = GeminiSummarizer()
        self.notifier = TelegramNotifier()

        # --- CONFIGURATION LOGIC ---
        self.start_date = os.getenv("START_DATE")
        self.end_date = os.getenv("END_DATE")
        self.max_items = int(os.getenv("MAX_ITEMS_TO_PROCESS", 0))

        if not (self.start_date and self.end_date):
            self.lookback_hours = int(os.getenv("LOOKBACK_HOURS", 24))
            self.logger.info(
                f"🔧 Config: Real-time mode. Lookback period set to {self.lookback_hours} hours."
            )
        else:
            self.logger.info(
                f"🔧 Config: Test/Backfill mode. Fetching from {self.start_date} to {self.end_date}."
            )
            if self.max_items > 0:
                self.logger.info(
                    f"🔧 Config: Limiting this run to a maximum of {self.max_items} new items."
                )

        if self.test_mode:
            self.logger.warning(
                "--- SCRAPER RUNNING IN TEST MODE: PDF downloads & Summarization are DISABLED. ---"
            )

    def _get_api_params(self):
        """Prepares parameters for the API call based on the mode."""
        if self.start_date and self.end_date:
            from_date_str, to_date_str = self.start_date, self.end_date
        else:
            to_date = datetime.now()
            from_date = to_date - timedelta(hours=self.lookback_hours)
            from_date_str, to_date_str = (
                from_date.strftime("%Y%m%d"),
                to_date.strftime("%Y%m%d"),
            )
        return {
            "pageno": 1,
            "strCat": "Company Update",
            "strPrevDate": from_date_str,
            "strScrip": "",
            "strSearch": "P",
            "strToDate": to_date_str,
            "strType": "C",
            "subcategory": "Earnings Call Transcript",
        }

    def _make_api_request(self, params, retries=3, backoff_factor=5):
        """A resilient method to make an API request with retries."""
        for attempt in range(retries):
            try:
                response = requests.get(
                    self.api_url, headers=self.headers, params=params, timeout=60
                )
                response.raise_for_status()
                return response.json()
            except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
                self.logger.warning(
                    f"Attempt {attempt + 1}/{retries} failed for page {params.get('pageno', 1)}: {e}"
                )
                if attempt + 1 == retries:
                    self.logger.error(
                        f"All {retries} retries failed for page {params.get('pageno', 1)}. Giving up."
                    )
                    return None
                wait_time = backoff_factor * (2**attempt)
                self.logger.info(f"Waiting for {wait_time} seconds before retrying...")
                time.sleep(wait_time)

    def fetch_announcements(self):
        """Fetches 'Earnings Call Transcript' announcements with smarter pagination and retries."""
        all_announcements = []
        params = self._get_api_params()
        self.logger.info("📡 Initial fetch to get total count...")

        initial_data = self._make_api_request(params)
        if not initial_data:
            return []

        total_records = initial_data.get("Table1", [{}])[0].get("ROWCNT", 0)
        if total_records == 0:
            self.logger.info("✔️ No records found for this period.")
            return []

        self.logger.info(f"✔️ Total records to fetch: {total_records}")
        records_this_page = initial_data.get("Table", [])
        all_announcements.extend(records_this_page)

        records_per_page = len(records_this_page)
        if records_per_page == 0:
            return []

        total_pages = (total_records + records_per_page - 1) // records_per_page

        for page_no in range(2, total_pages + 1):
            self.logger.info(f"📡 Fetching page {page_no}/{total_pages}...")
            params["pageno"] = page_no
            page_data = self._make_api_request(params)

            if not page_data or not page_data.get("Table"):
                self.logger.error(
                    f"Failed to retrieve data for page {page_no}. Stopping pagination."
                )
                break

            all_announcements.extend(page_data.get("Table", []))

        self.logger.info(
            f"✔️ Fetched a total of {len(all_announcements)} announcements."
        )
        return all_announcements

    def get_pdf_url_from_xbrl(self, news_id, scrip_code):
        params = {"Bsenewid": news_id, "Scripcode": scrip_code}
        try:
            response = requests.get(
                self.xbrl_base_url, params=params, headers=self.headers, timeout=15
            )
            response.raise_for_status()
            root = etree.fromstring(response.content)
            for elem in root.getiterator():
                if not hasattr(elem.tag, "find"):
                    continue
                i = elem.tag.find("}")
                if i >= 0:
                    elem.tag = elem.tag[i + 1 :]
            pdf_url_element = root.find(".//AttachmentURL")
            if pdf_url_element is not None and pdf_url_element.text:
                return pdf_url_element.text
            self.logger.warning(f"⚠️ Could not find AttachmentURL for NEWSID {news_id}")
            return None
        except (requests.exceptions.RequestException, etree.XMLSyntaxError) as e:
            self.logger.error(
                f"❌ Error fetching/parsing XBRL for NEWSID {news_id}: {e}"
            )
            return None

    def download_pdf(
        self, pdf_url: str, scrip_code: str, company_name: str, news_id: str
    ) -> Path | None:
        """
        If in test_mode, logs the URL and returns None.
        Otherwise, downloads the PDF from a web URL OR directly accesses a local file URI
        and returns its Path.
        """
        if self.test_mode:

            log_entry = f"{datetime.now().isoformat()} | {company_name} | {pdf_url}\n"
            with open(self.url_log_file, "a") as f:
                f.write(log_entry)
            self.logger.info(f"📝 Logged URL for {company_name}")
            return None

        # --- FIX ---
        if pdf_url.startswith("file://"):
            # This is a local file from our test harness. No download needed.
            try:
                # Convert the file URI to a standard system path
                local_path = Path(urlparse(pdf_url).path)
                if local_path.exists():
                    self.logger.info(f"✅ Accessed local test PDF: {local_path}")
                    # We need to copy it to the downloads folder to simulate a download
                    safe_name = "".join(
                        [c for c in company_name if c.isalnum() or c.isspace()]
                    ).rstrip()
                    filename = f"{scrip_code}_{safe_name}_{news_id[:8]}.pdf"
                    filepath = self.download_path / filename
                    return local_path
                else:
                    self.logger.error(f"❌ Local test PDF not found at {local_path}")
                    return None
            except Exception as e:
                self.logger.error(f"❌ Failed to handle local file URI {pdf_url}: {e}")
                return None
        else:
            # This is a real web URL. Use requests to download it.
            try:
                self.logger.info(f"⬇️ Downloading PDF for {company_name} from {pdf_url}")
                response = requests.get(
                    pdf_url, headers=self.headers, timeout=60, stream=True
                )
                response.raise_for_status()
                safe_name = "".join(
                    [c for c in company_name if c.isalnum() or c.isspace()]
                ).rstrip()
                filename = f"{scrip_code}_{safe_name}_{news_id[:8]}.pdf"
                filepath = self.download_path / filename
                with open(filepath, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                self.logger.info(f"💾 Saved PDF to {filepath}")
                return filepath
            except requests.exceptions.RequestException as e:
                self.logger.error(
                    f"❌ Failed to download PDF for NEWSID {news_id}: {e}"
                )
                return None

    async def process_and_summarize(self, pdf_path, news_id, company_name, pdf_url):
        """
        Orchestrates processing and RETURNS a factory function for the notification coroutine.
        """
        if self.test_mode:
            self.logger.info("Summarization skipped in test mode.")
            return None

        if not self.db.needs_summarization(news_id):
            self.logger.info(
                f"🔵 Item {news_id} already processed/summarized. Skipping."
            )
            return None

        self.logger.info(f"⚙️ Processing PDF for {company_name} ({news_id})...")
        content_data = self.pdf_processor.process_pdf(pdf_path)

        summary_data = await self.summarizer.summarize(
            content_data, company_name, pdf_url
        )

        status = (
            "PROCESSED" if summary_data.get("type") != "error" else "ERROR_PROCESSING"
        )
        self.db.update_summary(news_id, summary_data, status)
        self.logger.info(f"💾 Updated database for {news_id} with status: {status}")

        if status == "PROCESSED":
            # Return a factory (lambda) that creates the notification coroutine when called.
            if summary_data["type"] == "summary":
                return lambda: self.notifier.notify_summary(summary_data)
            elif summary_data["type"] == "web_link":
                return lambda: self.notifier.notify_weblink(summary_data)

        #  handle error notifications created by the summarizer
        elif summary_data.get("type") == "error":
            return lambda: self.notifier.notify_error(summary_data)

        return None  # Return None if no notification is needed

    async def run_all_notifications_sequentially(
        self, tasks: list[Callable[[], Awaitable[None]]]
    ) -> None:
        """Runs notification tasks one by one with a 2-second delay between each."""
        if not tasks:
            return
        total = len(tasks)
        self.logger.info(
            "--- Sending %s notifications sequentially (1 per 2 s) ---", total
        )
        for idx, task_factory in enumerate(tasks, 1):
            self.logger.info("  -> notification %s/%s", idx, total)
            await task_factory()  # create + await the coroutine here
            if idx < total:
                await asyncio.sleep(2)
        self.logger.info("--- All notifications sent successfully ---")

    async def run(self, announcements_override=None) -> list:
        self.logger.info("--- Starting BSE Scraper Run ---")

        # Allow injecting announcements for testing
        announcements = (
            announcements_override
            if announcements_override is not None
            else self.fetch_announcements()
        )

        if not announcements:
            self.logger.info("--- No announcements found. Ending run. ---")
            return []

        new_items_processed = 0
        # This list will now store functions that create notification coroutines
        notification_tasks: list[Callable[[], Awaitable[None]]] = []

        for item in reversed(announcements):
            if self.max_items > 0 and new_items_processed >= self.max_items:
                self.logger.warning(
                    f"🛑 Reached processing limit of {self.max_items}. Halting run."
                )
                break

            news_id = item.get("NEWSID")
            scrip_code = item.get("SCRIP_CD")
            name = item.get("SLONGNAME", "N/A").strip()

            if not news_id:
                continue
            if self.db.is_processed(news_id) and not self.db.needs_summarization(
                news_id
            ):
                continue

            pdf_path = None
            pdf_url = item.get("PDF_URL_OVERRIDE")  # For testing

            if not self.db.is_processed(news_id):
                new_items_processed += 1
                self.logger.info(f"✨ New item found for {name} ({news_id})")

                if not pdf_url:  # Get URL only if not provided by test override
                    pdf_url = self.get_pdf_url_from_xbrl(news_id, scrip_code)

                if pdf_url:
                    pdf_path = self.download_pdf(
                        pdf_url, str(scrip_code), name, news_id
                    )
                    if pdf_path:
                        self.db.add_new_announcement(news_id, str(scrip_code), name)
                    elif self.test_mode or item.get(
                        "is_test"
                    ):  # also add to db in our new test mode
                        self.db.add_new_announcement(news_id, str(scrip_code), name)

            if pdf_path or (
                self.db.is_processed(news_id) and self.db.needs_summarization(news_id)
            ):
                if not pdf_path:
                    safe_name = "".join(
                        [c for c in name if c.isalnum() or c.isspace()]
                    ).rstrip()
                    filename = f"{scrip_code}_{safe_name}_{news_id[:8]}.pdf"
                    pdf_path = self.download_path / filename

                if pdf_path.exists():
                    # Get the URL again if this is a resumable run
                    if not pdf_url:
                        pdf_url = self.get_pdf_url_from_xbrl(news_id, scrip_code) or ""

                    # This now returns a function (factory) that we can call later
                    notification_task_factory = await self.process_and_summarize(
                        pdf_path, news_id, name, pdf_url
                    )
                    if notification_task_factory:
                        notification_tasks.append(notification_task_factory)
                else:
                    self.logger.warning(
                        f"⚠️ PDF for {name} ({news_id}) not found, cannot process."
                    )

        if notification_tasks:
            self.logger.info(
                f"📦 Scraper run produced {len(notification_tasks)} notification tasks to be sent."
            )

        if new_items_processed == 0:
            self.logger.info(
                "✔️ No *new* announcements to download. All items up-to-date."
            )
        else:
            action = "logged URLs for" if self.test_mode else "processed"
            self.logger.info(
                f"✨ Run complete. Found and {action} {new_items_processed} new announcements."
            )
        self.logger.info("--- BSE Scraper Run Finished ---")
        return notification_tasks
