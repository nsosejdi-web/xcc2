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

    for key in keywords:
        await try_locator_click(page.locator(f'text="{key}"'), key)
        await try_locator_click(page.locator(f'text=/{re.escape(key)}/i'), key)

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


async def collect_usernames(page, tweet_author: str = None):
    found = set()

    selectors = [
        'article a[href^="/"][role="link"]',
        'a[href^="/"][role="link"]',
        'article a[href^="/"]',
        'a[href^="/"]',
    ]

    for selector in selectors:
        try:
            elements = await page.query_selector_all(selector)
        except:
            continue

        for el in elements:
            try:
                href = await el.get_attribute("href")
            except:
                href = None

            if not href:
                continue

            href = href.strip()

            if href.startswith((
                "/i/", "/home", "/explore", "/search", "/messages",
                "/notifications", "/compose", "/settings", "/jobs",
                "/tos", "/privacy", "/account"
            )):
                continue

            parts = href.strip("/").split("/")
            if not parts:
                continue

            username = parts[0].lower()

            if username in BLOCKED_USERS:
                continue

            # filter out tweet author — they appear on every reply card
            if tweet_author and username == tweet_author.lower():
                continue

            if USERNAME_RE.fullmatch(username):
                found.add(username)

    return found


async def get_comment_status(tweet_url, usernames, visible=False):
    print("📱 Mobile View Mode — strict reply scan with spam click")

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

        try:
            await click_exact_spam_button(page)
        except Exception as e:
            print("Initial spam click failed:", e)

        try:
            clicked = await click_reply_buttons(page)
            if clicked:
                print(f"🟢 Initial clicked buttons: {clicked}")
        except Exception as e:
            print("Initial reply-button scan failed:", e)

        while scroll_pass < 70:
            scroll_pass += 1

            clicked_1 = await click_reply_buttons(page)

            current_found = await collect_usernames(page, tweet_author)
            repliers.update(current_found)

            await page.mouse.wheel(0, 900)
            await asyncio.sleep(2.0)

            clicked_2 = await click_reply_buttons(page)

            for _ in range(4):
                await page.mouse.wheel(0, 420)
                await asyncio.sleep(0.8)

            clicked_3 = await click_reply_buttons(page)

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

            if idle_rounds >= 2:
                try:
                    again = await click_exact_spam_button(page)
                    if again:
                        idle_rounds = 0
                        print("🟣 Re-clicked exact spam button after idle")
                except:
                    pass

            # stop after 4 consecutive idle rounds
            if idle_rounds >= 4:
                print("🔻 No new usernames for 4 rounds. Stopping.")
                break

        await context.close()
        await browser.close()

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
