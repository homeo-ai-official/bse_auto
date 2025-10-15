import os
import telegram
import logging
from datetime import datetime
from zoneinfo import ZoneInfo  # Modern import for timezones

logger = logging.getLogger()

# Define the timezone 
IST = ZoneInfo("Asia/Kolkata")


class TelegramNotifier:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id_summaries = os.getenv("TELEGRAM_CHAT_ID_SUMMARIES")
        self.chat_id_links = os.getenv("TELEGRAM_CHAT_ID_LINKS")
        self.is_enabled = bool(
            self.bot_token and self.chat_id_summaries and self.chat_id_links
        )
        if self.is_enabled:
            logger.info("✅ Telegram Notifier initialized successfully.")
        else:
            logger.warning(
                "⚠️ Telegram Notifier is DISABLED. Missing token or chat ID in .env file."
            )

    def _escape_markdown(self, text: str) -> str:
        """
        Helper function to escape all reserved characters for Telegram's MarkdownV2.
        """
        escape_chars = r"_*[]()~`>#+-=|{}.!"
        for char in escape_chars:
            text = text.replace(char, f"\\{char}")
        return text

    async def _send_message(self, chat_id: str, message: str, parse_mode="MarkdownV2"):
        """A resilient method to send a message, with a fallback to plain text."""
        if not self.is_enabled:
            return
        try:
            # Create a new Bot instance for each message.
            bot = telegram.Bot(token=self.bot_token)
            logger.info("📤 Sending notification to chat ID %s.", chat_id)
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            logger.info("✅ Successfully sent notification to chat ID %s.", chat_id)
        except telegram.error.BadRequest as e:
            logger.error(
                "❌ Telegram BadRequest for chat %s (likely a Markdown error): %s",
                chat_id,
                e,
            )
            logger.error("📄 Message causing error:\n%s", message)
            if parse_mode == "MarkdownV2":
                logger.info("🔄 Retrying with plain text fallback...")
                await self._send_message(chat_id, message, parse_mode=None)
        except Exception as e:
            logger.exception(
                "❌ Failed to send Telegram notification to %s: %s", chat_id, e
            )

    async def notify_summary(self, summary_data: dict) -> None:
        """Sends summary to boss with the correct link."""
        company = summary_data.get("company_name", "Unknown Company").strip()
        escaped_company = self._escape_markdown(company)
        sentiment = self._escape_markdown(summary_data.get("sentiment", "Neutral"))
        points = summary_data.get("summary_points", [])
        formatted_points = "\n".join([f"• {self._escape_markdown(p)}" for p in points])

        timestamp_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M %Z")
        timestamp = self._escape_markdown(timestamp_str)
        separator = self._escape_markdown("=" * 20)

        
        original_pdf_url = summary_data.get("original_pdf_url", "")
        inner_links = summary_data.get("links", [])

        # Always include the original PDF link
        link_section = ""
        if original_pdf_url:
            safe_url = original_pdf_url.replace("(", "%28").replace(")", "%29")
            link_section = f"\n*Original PDF:* [Link]({safe_url})"

        # Also include inner link if available
        if inner_links:
            inner_url = inner_links[0]["url"].replace("(", "%28").replace(")", "%29")
            link_section += f"\n*Inner Link:* [Link]({inner_url})"
        

        message = (
            f"📊 *New AI Summary: {escaped_company}*\n"
            f"{separator}\n\n"
            f"*Sentiment:* `{sentiment}`\n\n"
            f"*Key Points:*\n"
            f"{formatted_points}"
            f"{link_section}\n\n"
            f"_{timestamp}_"
        )

        await self._send_message(self.chat_id_summaries, message)

    async def notify_error(self, error_data: dict) -> None:
        """Sends error notification to developer."""
        company = error_data.get("company_name", "Unknown")
        error_msg = error_data.get("message", "Unknown error")
        pdf_url = error_data.get("pdf_url", "")

        timestamp = self._escape_markdown(
            datetime.now(IST).strftime("%Y-%m-%d %H:%M %Z")
        )

        url_section = ""
        if pdf_url:
            safe_url = pdf_url.replace("(", "%28").replace(")", "%29")
            url_section = f"\n*PDF URL:* [Link]({safe_url})"

        message = (
            f"❌ *Error Processing PDF*\n"
            f"Company: {self._escape_markdown(company)}\n"
            f"Error: `{self._escape_markdown(error_msg)}`"
            f"{url_section}\n"
            f"_{timestamp}_"
        )
        await self._send_message(self.chat_id_links, message)

    async def notify_weblink(self, link_data: dict) -> None:
        """Sends web link notification to developer."""
        company = link_data.get("company_name", "Unknown Company").strip()
        escaped_company = self._escape_markdown(company)
        links = link_data.get("links", [])
        original_pdf_url = link_data.get("original_pdf_url", "")

        if not links:
            return

        timestamp_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M %Z")
        timestamp = self._escape_markdown(timestamp_str)

        original_link_section = ""
        if original_pdf_url:
            safe_pdf_url = original_pdf_url.replace("(", "%28").replace(")", "%29")
            original_link_section = f"*BSE PDF:* [Link]({safe_pdf_url})\n"

        web_links = "\n".join(
            [
                f"*_[WEB]({link['url'].replace('(', '%28').replace(')', '%29')})_*"
                for link in links
            ]
        )

        message = (
            f"🔗 *Web Link Found: {escaped_company}*\n\n"
            f"{original_link_section}"
            f"*Web Links:*\n{web_links}\n\n"
            f"_{timestamp}_"
        )

        await self._send_message(self.chat_id_links, message)
