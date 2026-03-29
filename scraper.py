import asyncio
import re
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

BLOCKED_USERS = {
    "compose", "explore", "home", "i", "intent", "login", "messages",
    "notifications", "search", "settings", "share", "tos", "privacy",
    "x", "twitter"
}


async def safe_click(page, el, pause=2.5):
    try:
        await el.scroll_into_view_if_needed()
    except:
        pass

    await asyncio.sleep(0.6)

    try:
        await el.click(timeout=3000)
        await asyncio.sleep(pause)
        return True
    except:
        pass

    try:
        handle = await el.element_handle() if hasattr(el, "element_handle") else el
        await page.evaluate("(e) => e.click()", handle)
        await asyncio.sleep(pause)
        return True
    except:
        return False


async def click_exact_spam_button(page):
    targets = [
        'text="Show probable spam"',
        'text=/show probable spam/i',
    ]

    for sel in targets:
        try:
            locator = page.locator(sel)
            count = await locator.count()
            if count > 0:
                for i in range(count):
                    el = locator.nth(i)
                    ok = await safe_click(page, el, pause=3)
                    if ok:
                        print("🟣 Clicked exact 'Show probable spam'")
                        return True
        except:
            pass

    return False


async def click_reply_buttons(page):
    clicked = 0

    keywords = [
        "show probable spam",
        "show hidden replies",
        "view hidden replies",
        "view more replies",
        "show more replies",
        "show additional replies",
        "load more replies",
        "load earlier replies",
        "show replies",
        "show more",
        "view replies",
    ]

    async def try_locator_click(locator, label):
        nonlocal clicked
        try:
            count = await locator.count()
            if count <= 0:
                return

            for i in range(count):
                el = locator.nth(i)
                ok = await safe_click(page, el)
                if ok:
                    clicked += 1
                    print(f"🟢 Clicked: {label}")
        except:
            pass

    # 1) exact / regex text locators
    for key in keywords:
        await try_locator_click(page.locator(f'text="{key}"'), key)
        await try_locator_click(page.locator(f'text=/{re.escape(key)}/i'), key)

    # 2) buttons / links / spans manual scan
    try:
        elements = await page.query_selector_all('div[role="button"], a, span')
        for el in elements:
            parts = []

            try:
                t = await el.inner_text()
                if t:
                    parts.append(t.strip().lower())
            except:
                pass

            try:
                a = await el.get_attribute("aria-label")
                if a:
                    parts.append(a.strip().lower())
            except:
                pass

            try:
                h = await el.inner_html()
                if h:
                    parts.append(h.lower())
            except:
                pass

            combined = " ".join(parts)

            if any(k in combined for k in keywords):
                ok = await safe_click(page, el)
                if ok:
                    clicked += 1
                    print(f"🟢 Clicked via fallback: {combined[:80]}")
    except:
        pass

    return clicked


def extract_username_from_href(href: str) -> str | None:
    """Extract a valid Twitter username from a href string."""
    if not href:
        return None

    href = href.strip()

    # skip obvious non-profile routes
    if href.startswith((
        "/i/", "/home", "/explore", "/search", "/messages",
        "/notifications", "/compose", "/settings", "/jobs",
        "/tos", "/privacy", "/account"
    )):
        return None

    parts = href.strip("/").split("/")
    if not parts:
        return None

    username = parts[0].lower()

    if username in BLOCKED_USERS:
        return None

    if USERNAME_RE.fullmatch(username):
        return username

    return None


async def collect_usernames(page, tweet_author: str = None):
    """
    Collect usernames ONLY from reply articles (skipping the first article
    which is the main tweet). Falls back to full-page scan only if no reply
    articles are found.
    """
    found = set()

    # --- Strategy 1: reply articles only (skip first article = main tweet) ---
    try:
        all_articles = await page.query_selector_all('article')
        # First article is the main tweet; replies start from index 1
        reply_articles = all_articles[1:] if len(all_articles) > 1 else []

        for article in reply_articles:
            try:
                links = await article.query_selector_all('a[href^="/"]')
            except:
                continue

            for el in links:
                try:
                    href = await el.get_attribute("href")
                    username = extract_username_from_href(href)
                    if username:
                        # Skip tweet author appearing in reply cards
                        if tweet_author and username == tweet_author.lower():
                            continue
                        found.add(username)
                except:
                    pass

        if found:
            return found
    except:
        pass

    # --- Strategy 2: fallback — role=link scoped to articles ---
    try:
        elements = await page.query_selector_all('article a[href^="/"][role="link"]')
        for el in elements:
            try:
                href = await el.get_attribute("href")
                username = extract_username_from_href(href)
                if username:
                    found.add(username)
            except:
                pass

        if found:
            return found
    except:
        pass

    # --- Strategy 3: last resort — full page scan ---
    try:
        elements = await page.query_selector_all('a[href^="/"]')
        for el in elements:
            try:
                href = await el.get_attribute("href")
                username = extract_username_from_href(href)
                if username:
                    found.add(username)
            except:
                pass
    except:
        pass

    return found


async def get_comment_status(tweet_url, usernames, visible=False):
    print("📱 Mobile View Mode — strict reply scan with spam click")

    # Extract tweet author from URL so we can filter them out of reply collection
    tweet_author = None
    author_match = re.search(r'x\.com/([^/]+)/status/', tweet_url)
    if author_match:
        tweet_author = author_match.group(1).lower()
        print(f"🐦 Tweet author: @{tweet_author}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=visible,
            slow_mo=100,
            args=["--no-sandbox"]
        )

        context = await browser.new_context(
            storage_state="auth.json",
            viewport={"width": 390, "height": 844},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 15_2 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.2 Mobile/15E148 Safari/604.1"
            ),
            device_scale_factor=2.0,
            is_mobile=True,
            has_touch=True,
        )

        page = await context.new_page()

        try:
            print(f"🌐 Opening Tweet: {tweet_url}")
            await page.goto(tweet_url, wait_until="domcontentloaded", timeout=60000)

            print("⏳ Waiting initial 10 seconds...")
            await asyncio.sleep(10)
        except PlaywrightTimeoutError:
            print("⚠️ Timeout while loading tweet.")
            await context.close()
            await browser.close()
            return "❌ Could not load the Tweet. Please check the link."

        repliers = set()
        last_count = 0
        idle_rounds = 0
        scroll_pass = 0

        print("🔄 Scanning replies...")

        # initial exact spam click
        try:
            await click_exact_spam_button(page)
        except Exception as e:
            print("Initial spam click failed:", e)

        # initial generic button click
        try:
            clicked = await click_reply_buttons(page)
            if clicked:
                print(f"🟢 Initial clicked buttons: {clicked}")
        except Exception as e:
            print("Initial reply-button scan failed:", e)

        while scroll_pass < 70:
            scroll_pass += 1

            # before collect
            clicked_1 = await click_reply_buttons(page)

            current_found = await collect_usernames(page, tweet_author)
            repliers.update(current_found)

            # main scroll
            await page.mouse.wheel(0, 900)
            await asyncio.sleep(2.4)

            # try clicking again after big scroll
            clicked_2 = await click_reply_buttons(page)

            # smaller step scrolls
            for _ in range(5):
                await page.mouse.wheel(0, 420)
                await asyncio.sleep(0.9)

            # try again after mini scrolls
            clicked_3 = await click_reply_buttons(page)

            # collect after scrolling/clicking
            current_found = await collect_usernames(page, tweet_author)
            repliers.update(current_found)

            total_clicked = clicked_1 + clicked_2 + clicked_3
            print(
                f"Scroll {scroll_pass}/70 → {len(repliers)} usernames found "
                f"(clicked {total_clicked})"
            )

            if len(repliers) == last_count:
                idle_rounds += 1
            else:
                idle_rounds = 0
                last_count = len(repliers)

            # if we are stuck, try exact spam click once more
            if idle_rounds >= 2:
                try:
                    again = await click_exact_spam_button(page)
                    if again:
                        idle_rounds = 0
                        print("🟣 Re-clicked exact spam button after idle")
                except:
                    pass

            if idle_rounds >= 3:
                print("🔻 No new usernames for 3 rounds. Stopping.")
                break

        await context.close()
        await browser.close()

    # save usernames
    with open("repliers_list.txt", "w", encoding="utf-8") as f:
        for u in sorted(repliers):
            f.write(f"@{u}\n")

    print(f"✅ Total repliers found: {len(repliers)}")
    print("📋 Sample:", sorted(list(repliers))[:40])

    all_users = [u.lower().lstrip("@").strip() for u in usernames]
    commented = [u for u in all_users if u in repliers]
    not_commented = [u for u in all_users if u not in repliers]

    def fmt(lst):
        return "\n".join(f"@{u}" for u in lst) if lst else "—"

    result = (
        f"✅ Commented:\n{fmt(commented)}\n\n"
        f"❌ Not Commented:\n{fmt(not_commented)}\n\n"
        f"📊 Summary:\n"
        f"Total Users: {len(all_users)}\n"
        f"Commented: {len(commented)}\n"
        f"Not Commented: {len(not_commented)}\n"
        f"🧩 Total Replies Found: {len(repliers)}"
    )

    return result
