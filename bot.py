import asyncio
import re
import logging
import os
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from scraper import get_comment_status
from playwright.async_api import async_playwright

# ===================== CONFIG =====================
BOT_TOKEN = os.getenv("8436924280:AAEvgqJr-aJFu_YLtL6Ogw0pE1FAgSgdFlY", "").strip()
COLLECT_DELAY = 2

# comma separated env var: ALLOWED_USERS=12345,67890
allowed_users_raw = os.getenv("ALLOWED_USERS", "").strip()
ALLOWED_USERS = set(5542815933)

if allowed_users_raw:
    for item in allowed_users_raw.split(","):
        item = item.strip()
        if item.isdigit():
            ALLOWED_USERS.add(int(item))

logging.basicConfig(level=logging.INFO)
router = Router()
bot_instance: Bot = None

pending_tasks: dict = {}
processing_sent: dict = {}

# ===================== STATES =====================
class Flow(StatesGroup):
    collecting_member_list = State()
    waiting_for_tweet_count = State()
    waiting_for_tweet_links = State()

# ===================== HELPERS =====================

def ensure_auth_file_exists() -> None:
    """
    Ensures auth.json exists.
    You must provide auth.json in the repo OR generate it another way before run.
    """
    if not os.path.exists("auth.json"):
        raise FileNotFoundError(
            "auth.json not found. Add a valid auth.json file before starting the bot."
        )

def validate_env() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is missing.")
    if not ALLOWED_USERS:
        raise ValueError("ALLOWED_USERS environment variable is missing or invalid.")
    ensure_auth_file_exists()

async def resolve_i_status_urls(url_list: list) -> dict:
    i_links = list(set(u for u in url_list if "/i/status/" in u))
    result = {}

    if not i_links:
        return result

    logging.info("Resolving %s /i/status/ links...", len(i_links))

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            storage_state="auth.json",
            viewport={"width": 390, "height": 800},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 15_2 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.2 Mobile/15E148 Safari/604.1"
            ),
            is_mobile=True,
        )

        for url in i_links:
            page = None
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(4)

                username = None
                final_url = page.url

                match = re.search(r"x\.com/([^/]+)/status/", final_url)
                if match and match.group(1).lower() != "i":
                    username = match.group(1).lower()

                if not username:
                    try:
                        author_el = await page.query_selector(
                            'article div[data-testid="User-Name"] a[href^="/"]'
                        )
                        if author_el:
                            href = await author_el.get_attribute("href")
                            if href:
                                candidate = href.strip("/").split("/")[0].lower()
                                if re.fullmatch(r"[a-z0-9_]{1,50}", candidate):
                                    if candidate not in ("i", "home", "explore"):
                                        username = candidate
                    except Exception:
                        pass

                if not username:
                    try:
                        status_id_match = re.search(r"/status/(\d+)", url)
                        if status_id_match:
                            status_id = status_id_match.group(1)
                            html = await page.content()
                            match2 = re.search(
                                rf'"([A-Za-z0-9_]{{1,50}})/status/{status_id}"',
                                html
                            )
                            if not match2:
                                match2 = re.search(
                                    rf"/([A-Za-z0-9_]{{1,50}})/status/{status_id}",
                                    html
                                )
                            if match2:
                                candidate = match2.group(1).lower()
                                if candidate not in ("i", "home", "explore"):
                                    username = candidate
                    except Exception:
                        pass

                result[url] = username

            except Exception as e:
                logging.exception("Error resolving url %s: %s", url, e)
                result[url] = None
            finally:
                if page:
                    await page.close()

        await context.close()
        await browser.close()

    return result


async def resolve_tweet_url_playwright(url: str) -> str:
    if "/i/status/" not in url:
        return url

    resolved_map = await resolve_i_status_urls([url])
    username = resolved_map.get(url)

    if username:
        status_id_match = re.search(r"/status/(\d+)", url)
        if status_id_match:
            return f"https://x.com/{username}/status/{status_id_match.group(1)}"

    return url


def parse_member_list(text: str, resolved_map: dict = None) -> list:
    members = []
    url_pattern = re.compile(r"(https?://(?:www\.)?(?:twitter|x)\.com/\S+)")
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        post_match = re.match(r"Post\s+(\d+):\s*@(\S+)\s+(\S+)", line, re.IGNORECASE)

        if post_match:
            post_num = int(post_match.group(1))
            tg_username = post_match.group(2)
            x_username_raw = post_match.group(3).lower()

            tweet_url = None
            for j in range(i + 1, min(i + 4, len(lines))):
                url_match = url_pattern.search(lines[j])
                if url_match:
                    tweet_url = url_match.group(1)
                    break

            if tweet_url:
                if "/i/status/" in tweet_url and resolved_map:
                    resolved_username = resolved_map.get(tweet_url)
                    if resolved_username:
                        x_username_raw = resolved_username
                elif "/i/status/" not in tweet_url:
                    url_user = re.search(r"x\.com/([^/]+)/status/", tweet_url)
                    if url_user:
                        x_username_raw = url_user.group(1).lower()

            members.append(
                {
                    "post": post_num,
                    "tg": tg_username,
                    "x": x_username_raw,
                    "tweet_url": tweet_url or "",
                }
            )
        i += 1

    return members

# ===================== HANDLERS =====================

def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS


@router.message(F.text == "/get_id")
async def get_id_cmd(msg: Message):
    await msg.answer(f"Your user ID: `{msg.from_user.id}`", parse_mode="Markdown")


@router.message(CommandStart())
async def start_cmd(msg: Message, state: FSMContext):
    if not is_allowed(msg.from_user.id):
        await msg.answer("You are not authorized.")
        return

    processing_sent.pop(msg.from_user.id, None)
    old = pending_tasks.pop(msg.from_user.id, None)
    if old:
        old.cancel()

    await state.clear()
    await state.set_state(Flow.collecting_member_list)
    await msg.answer("Send the user list.")


@router.message(Flow.collecting_member_list, F.text)
async def collect_member_list(msg: Message, state: FSMContext):
    if not is_allowed(msg.from_user.id):
        return

    user_id = msg.from_user.id
    chat_id = msg.chat.id
    text = msg.text.strip()

    data = await state.get_data()
    collected_text = data.get("collected_text", "")
    collected_text += "\n" + text
    await state.update_data(collected_text=collected_text)

    if user_id not in processing_sent:
        processing_sent[user_id] = True
        await msg.answer("Processing...")

    old_task = pending_tasks.pop(user_id, None)
    if old_task:
        old_task.cancel()

    async def delayed_process():
        try:
            await asyncio.sleep(COLLECT_DELAY)

            fresh_data = await state.get_data()
            full_text = fresh_data.get("collected_text", "")

            url_pattern = re.compile(r"(https?://(?:www\.)?(?:twitter|x)\.com/\S+)")
            all_urls = url_pattern.findall(full_text)
            i_status_urls = list(set(u for u in all_urls if "/i/status/" in u))

            if i_status_urls:
                await bot_instance.send_message(chat_id, f"Resolving {len(i_status_urls)} /i/ links...")
                resolved_map = await resolve_i_status_urls(i_status_urls)
            else:
                resolved_map = {}

            members = parse_member_list(full_text, resolved_map)

            if not members:
                await bot_instance.send_message(chat_id, "No members found.")
                return

            await state.update_data(members=members)
            await state.set_state(Flow.waiting_for_tweet_count)
            await bot_instance.send_message(
                chat_id,
                f"Found {len(members)} members. How many tweets to check? (1-5)"
            )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.exception("Delayed process failed: %s", e)
            await bot_instance.send_message(chat_id, f"Error: {e}")
        finally:
            pending_tasks.pop(user_id, None)
            processing_sent.pop(user_id, None)

    task = asyncio.create_task(delayed_process())
    pending_tasks[user_id] = task


@router.message(Flow.waiting_for_tweet_count, F.text)
async def get_tweet_count(msg: Message, state: FSMContext):
    if not is_allowed(msg.from_user.id):
        return

    text = msg.text.strip()

    if not text.isdigit() or int(text) < 1 or int(text) > 5:
        await msg.answer("Enter a number from 1 to 5.")
        return

    count = int(text)
    await state.update_data(tweet_count=count, tweet_links=[])
    await msg.answer("Send tweet link 1.")
    await state.set_state(Flow.waiting_for_tweet_links)


@router.message(Flow.waiting_for_tweet_links, F.text)
async def get_tweet_links(msg: Message, state: FSMContext):
    if not is_allowed(msg.from_user.id):
        return

    data = await state.get_data()
    tweet_links = data.get("tweet_links", [])
    tweet_count = data.get("tweet_count", 1)
    members = data.get("members", [])

    link = msg.text.strip()

    if not re.match(r"https?://(www\.)?(twitter|x)\.com/", link):
        await msg.answer("Invalid link.")
        return

    tweet_links.append(link)
    await state.update_data(tweet_links=tweet_links)

    if len(tweet_links) < tweet_count:
        await msg.answer(f"Send tweet link {len(tweet_links) + 1}.")
        return

    await msg.answer("Scanning... please wait.")

    all_x_usernames = {m["x"] for m in members}
    all_results = []

    for i, raw_link in enumerate(tweet_links):
        await msg.answer(f"Scanning tweet {i + 1}/{tweet_count}...")
        resolved_link = await resolve_tweet_url_playwright(raw_link)

        try:
            result_raw = await get_comment_status(
                resolved_link,
                all_x_usernames,
                visible=False
            )

            this_commented = set()
            commented_section = re.search(
                r"✅ Commented:\n(.*?)(?:\n\n|❌|$)",
                result_raw,
                re.DOTALL
            )
            if commented_section:
                for u in re.findall(r"@(\S+)", commented_section.group(1)):
                    this_commented.add(u.lower())

            all_results.append((i + 1, raw_link, this_commented))

        except Exception as e:
            logging.exception("Error scanning tweet %s: %s", i + 1, e)
            await msg.answer(f"Error scanning tweet {i + 1}: {e}")

    def format_member(m):
        return f"Post {m['post']}: @{m['tg']} ({m['x']})"

    full_result = ""
    for (link_num, link_url, commented) in all_results:
        commented_members = [m for m in members if m["x"] in commented]
        not_commented_members = [m for m in members if m["x"] not in commented]

        full_result += f"🔗 <b>Link {link_num}:</b> {link_url}\n\n"

        if commented_members:
            full_result += f"✅ <b>Commented ({len(commented_members)}):</b>\n"
            full_result += "\n".join(format_member(m) for m in commented_members)
            full_result += "\n\n"

        if not_commented_members:
            full_result += f"❌ <b>Not Commented ({len(not_commented_members)}):</b>\n"
            full_result += "\n".join(format_member(m) for m in not_commented_members)
            full_result += "\n\n"

        full_result += (
            f"📊 <b>Summary:</b>\n"
            f"Total: {len(members)} | "
            f"Commented: {len(commented_members)} | "
            f"Not Commented: {len(not_commented_members)}\n"
        )
        full_result += "\n" + "─" * 30 + "\n\n"

    if len(full_result) > 4096:
        chunks = [full_result[i:i + 4096] for i in range(0, len(full_result), 4096)]
        for chunk in chunks:
            await bot_instance.send_message(msg.chat.id, chunk, parse_mode="HTML")
    else:
        await bot_instance.send_message(msg.chat.id, full_result, parse_mode="HTML")

    processing_sent.pop(msg.from_user.id, None)
    await state.clear()
    await state.set_state(Flow.collecting_member_list)
    await msg.answer("Done. Send a new user list to check again.")


# ===================== MAIN =====================
async def main():
    global bot_instance

    validate_env()

    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    bot_instance = Bot(BOT_TOKEN)
    logging.info("Bot is running...")
    await dp.start_polling(bot_instance, drop_pending_updates=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
    except Exception as e:
        logging.exception("Fatal error: %s", e)
        raise
