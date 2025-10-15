import os
import json
import logging
import requests
from pathlib import Path
import time
import mimetypes
import random
from json import JSONDecodeError


from google import genai
from google.genai import types as genai_types


logger = logging.getLogger()


# ----------  HARNESS  ----------
def _gemini_call_with_retry(call_fn, *, desc="gemini_call", max_attempts=3):
    """
    Universal retry wrapper for ANY Gemini API call (text or media).
    call_fn()  ->  response object with .text attribute
    """
    for attempt in range(1, max_attempts + 1):
        time.sleep(1)
        try:
            resp = call_fn()
            if not resp or not resp.text or not resp.text.strip():
                raise ValueError("Empty response body")
            cleaned = (
                resp.text.strip().removeprefix("```json").removesuffix("```").strip()
            )
            return json.loads(cleaned)
        except (JSONDecodeError, ValueError) as exc:
            logger.warning(
                f"⚠️  {desc} failed (attempt {attempt}/{max_attempts}): {exc}"
            )
            if attempt == max_attempts:
                break
            sleep_time = 2**attempt + random.uniform(0, 1)  # exp-backoff + jitter
            time.sleep(sleep_time)
        except Exception as exc:  # catch-all so pipeline survives
            logger.exception(f"🔥 Unexpected error in {desc}")
            break
    # ---  after last failure return safe fallback  ---
    logger.error(f"❌ {desc} exhausted all retries – returning fallback error JSON")
    return None


class GeminiSummarizer:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not found in .env file.")

        # This is the modern way to interact with the API.
        self.client = genai.Client(api_key=self.api_key)

        # We will specify the model name in each call now.
        self.model_name = "gemini-flash-lite-latest"

        self.media_cache_path = Path("media_cache")
        self.media_cache_path.mkdir(exist_ok=True)

    # --- START INTEGRATION: EXTRACT COMPANY NAME ---
    def _extract_company_name_from_text(self, text: str) -> str:
        prompt = """
        You are a financial document analyst. Extract the **company name** from the following text.
        Return only the company name as a plain string. If unsure, return "Unknown Company".
        """
        try:
            resp = self.client.models.generate_content(
                model=self.model_name,
                contents=[prompt + "\n\n" + text[:5000]],  # first 5k chars is enough
            )
            return resp.text.strip() or "Unknown Company"
        except Exception as e:
            logger.warning(f"⚠️ Failed to extract company name: {e}")
            return "Unknown Company"

    # --- END INTEGRATION ---

    def _generate_text_prompt(self, transcript_text: str, company_name: str) -> str:
        # ### --- improved prompt --- ###
        return f"""
            You are a seasoned financial analyst’s assistant with over 50 years of experience in corporate analysis, equity research, and investment evaluation. You are assisting in analyzing an audio, video, or document file from a corporate announcement for the company '{company_name}'.

            Your goal is to deliver a highly insightful and investment-oriented summary that captures both present performance and future outlook.

            **Instructions:**
            1. **Comprehensive Review:** Carefully listen, watch, or read the full transcript to understand the company’s core message, tone, and direction.
            2. **Extract Key Insights:** Identify and emphasize:
            - Revenue, margins, and profitability trends  
            - Strategic initiatives and growth drivers  
            - Management commentary on future guidance and capital allocation  
            - Risks, challenges, and competitive positioning  
            - Market outlook and investment implications  
            3. **Investment Focus:** Provide insights relevant to potential investors, highlighting signals of strength, caution, or transition.
            4. **Summarization Rule:** Write the summary as 12–15 clear, cohesive sentences that reflect both data-driven analysis and interpretive judgment.
            5. **Sentiment Analysis:** Conclude with a single sentiment label that best reflects the tone and outlook of the company.

            **Sentiment Options (choose one):**
            - **Strongly Bullish** — clear confidence, robust growth outlook, strong financials  
            - **Moderately Bullish** — positive tone, steady growth with manageable risks  
            - **Neutral** — balanced tone, stable performance, limited new insights  
            - **Cautious/Bearish** — concerns over growth, margins, or market conditions  
            - **Strongly Bearish** — significant risks, weak outlook, negative sentiment

            **Output Format:**  
            Return a **single valid JSON object** in this format:
            {{
                "company_name": "{company_name}",
                "type": "summary",
                "summary_points": [
                    "Sentence 1",
                    "Sentence 2",
                    "... (up to 15 sentences)"
                ],
                "sentiment": "Strongly Bullish / Moderately Bullish / Neutral / Cautious/Bearish / Strongly Bearish"
            }}

            **Transcript:**
            ---
            {transcript_text}
            ---
            """

    def _generate_media_prompt(self, company_name: str) -> str:
        # This prompt is for media files and includes the company name
        return f"""
            You are a seasoned financial analyst’s assistant with over 50 years of experience in corporate analysis, equity research, and investment evaluation. You are assisting in analyzing an audio, video, or document file from a corporate announcement for the company '{company_name}'.

            Your goal is to deliver a highly insightful and investment-oriented summary that captures both present performance and future outlook.

            **Instructions:**
            1. **Comprehensive Review:** Carefully listen, watch, or read the full transcript to understand the company’s core message, tone, and direction.
            2. **Extract Key Insights:** Identify and emphasize:
            - Revenue, margins, and profitability trends  
            - Strategic initiatives and growth drivers  
            - Management commentary on future guidance and capital allocation  
            - Risks, challenges, and competitive positioning  
            - Market outlook and investment implications  
            3. **Investment Focus:** Provide insights relevant to potential investors, highlighting signals of strength, caution, or transition.
            4. **Summarization Rule:** Write the summary as 12–15 clear, cohesive sentences that reflect both data-driven analysis and interpretive judgment.
            5. **Sentiment Analysis:** Conclude with a single sentiment label that best reflects the tone and outlook of the company.

            **Sentiment Options (choose one):**
            - **Strongly Bullish** — clear confidence, robust growth outlook, strong financials  
            - **Moderately Bullish** — positive tone, steady growth with manageable risks  
            - **Neutral** — balanced tone, stable performance, limited new insights  
            - **Cautious/Bearish** — concerns over growth, margins, or market conditions  
            - **Strongly Bearish** — significant risks, weak outlook, negative sentiment

            **Output Format:**  
            Return a **single valid JSON object** in this format:
            {{
                "company_name": "{company_name}",
                "type": "summary",
                "summary_points": [
                    "Sentence 1",
                    "Sentence 2",
                    "... (up to 15 sentences)"
                ],
                "sentiment": "Strongly Bullish / Moderately Bullish / Neutral / Cautious/Bearish / Strongly Bearish"
            }}
            """

    # INTEGRATION: Method must be async to call notifier
    async def _summarize_media_from_url(
        self, media_url: str, company_name: str, original_pdf_url: str
    ) -> dict:
        """
        Downloads a media file and summarizes it using the new Gemini SDK.
        """
        filepath = None
        media_file = None  # Initialize media_file to ensure it's available in finally
        try:
            logger.info(f"⬇️ Downloading media for summarization from {media_url}")
            filename = media_url.split("/")[-1]

            if media_url.startswith("file://"):
                filepath = Path(media_url[7:])
                if not filepath.exists():
                    raise FileNotFoundError(f"Local test file not found: {filepath}")
                logger.info(f"📎 Using local test file: {filepath}")
            else:
                filepath = self.media_cache_path / filename
                response = requests.get(media_url, timeout=120, stream=True)
                response.raise_for_status()
                with open(filepath, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

            logger.info(f"🧠 Uploading '{filename}' to Gemini using the new SDK...")

            # FIX: Upload the file using the correct API
            with open(filepath, "rb") as f:
                mime_type, _ = mimetypes.guess_type(str(filepath))
                if not mime_type:
                    mime_type = "application/octet-stream"  # fallback
                media_file = self.client.files.upload(
                    file=f, config={"mime_type": mime_type}
                )

            # The polling logic
            while media_file.state.name == "PROCESSING":
                time.sleep(2)
                media_file = self.client.files.get(name=media_file.name)

            if media_file.state.name == "FAILED":
                raise Exception(f"Gemini file processing failed: {media_file.state}")

            # --- START MEDIA SUMMARIZATION ---
            logger.info(f"🗣️ Generating summary from media file for '{company_name}'...")
            prompt = self._generate_media_prompt(company_name)

            def _call():
                return self.client.models.generate_content(
                    model=self.model_name, contents=[prompt, media_file]
                )

            summary_json = _gemini_call_with_retry(
                _call, desc=f"media summary for {company_name}"
            )
            if summary_json is None:  # all retries failed
                return self._create_error_json(
                    "gemini_media_error",
                    "Gemini media summarisation failed after retries",
                    company_name,
                    original_pdf_url,
                )
            logger.info(
                f"✅ Successfully generated summary from media for '{company_name}'."
            )

            # Inject the inner link into the summary so notify_summary can use it
            summary_json["links"] = [{"url": media_url, "link_type": "media"}]

            # Ensure the original_pdf_url is in the successful summary response.
            summary_json["original_pdf_url"] = original_pdf_url
            return summary_json
            # --- END  ---

        except Exception as e:
            logger.error(
                f"❌ Media summarization failed for '{company_name}' (URL: {media_url}): {e}",
                exc_info=True,
            )
            return self._create_error_json(
                "media_summarization_error", str(e), company_name, original_pdf_url
            )
        finally:
            # Clean up the local downloaded file
            if filepath and filepath.exists() and not media_url.startswith("file://"):
                filepath.unlink()
            # Clean up the file on Google's servers.
            if media_file:
                self.client.files.delete(name=media_file.name)

    async def summarize(
        self, content_data: dict, company_name: str, original_pdf_url: str
    ) -> dict:
        """
        Orchestrates summarization based on content type.
        Can handle text, dispatch media summarization, or prepare web link notifications.
        """
        # --- START : COMPANY NAME FALLBACK ---
        if not company_name or company_name.strip().lower() == "n/a":
            logger.info(
                "Company name is missing or N/A, attempting to extract from text..."
            )
            company_name = self._extract_company_name_from_text(
                content_data.get("content", "")
            )
            logger.info(f"Extracted company name: '{company_name}'")
        # --- END  ---

        content_type = content_data.get("type")

        # --- START : TEXT SUMMARIZATION ---
        if content_type == "text":
            prompt = self._generate_text_prompt(content_data["content"], company_name)
            logger.info(
                f"🧠 Sending text for '{company_name}' to Gemini for summarisation..."
            )

            def _call():
                return self.client.models.generate_content(
                    model=self.model_name, contents=[prompt]
                )

            summary_json = _gemini_call_with_retry(
                _call, desc=f"text summary for {company_name}"
            )
            if summary_json is None:  # all retries failed
                return self._create_error_json(
                    "gemini_text_error",
                    "Gemini text summarisation failed after retries",
                    company_name,
                    original_pdf_url,
                )
            logger.info(f"✅ Successfully generated summary for '{company_name}'.")

            # Mark it as coming from the original PDF directly
            summary_json["links"] = []
            summary_json["original_pdf_url"] = original_pdf_url

            return summary_json
        # --- END  ---

        elif content_type == "link":
            links = content_data.get("links", [])
            web_links = [link for link in links if link.get("link_type") == "web"]
            media_links = [link for link in links if link.get("link_type") == "media"]

            if media_links:
                logger.info(
                    f"Detected media link for '{company_name}'. Initiating stage-2 summarization."
                )
                media_url = media_links[0]["url"]
                # The called method now handles adding the original_pdf_url
                return await self._summarize_media_from_url(
                    media_url, company_name, original_pdf_url
                )

            elif web_links:
                logger.info(
                    f"🔗 PDF for '{company_name}' contained external web links. Recording link(s)."
                )

                return {
                    "company_name": company_name,
                    "type": "web_link",
                    "links": web_links,
                    "original_pdf_url": original_pdf_url,
                }

            # This case handles PDFs processed but containing neither media nor web links.
            logger.warning(
                f"⚠️ PDF for '{company_name}' has no actionable media/web links."
            )
            return self._create_error_json(
                "no_actionable_content",
                "Small PDF with no actionable content",
                company_name,
                original_pdf_url,
            )

        else:
            logger.error(
                f"❗️ Cannot summarize due to processing error for '{company_name}'."
            )
            return self._create_error_json(
                "processing_error",
                content_data.get("message"),
                company_name,
                original_pdf_url,
            )

    # --- CHANGE IMPLEMENTED ---
    # Modified this helper function to accept and include the pdf_url directly,
    # making it impossible to forget when creating an error object.
    def _create_error_json(
        self, error_type: str, message: str, company_name: str, pdf_url: str
    ) -> dict:
        return {
            "company_name": company_name,
            "type": "error",
            "error_type": error_type,
            "message": message,
            "original_pdf_url": pdf_url,
        }
