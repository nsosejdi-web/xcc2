import asyncio
import re
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


async def get_comment_status(tweet_url, usernames, visible=False):
    print("mobile scan mode started")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not visible,
            slow_mo=120,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        context = await browser.new_context(
            storage_state="auth.json",
            viewport={"width": 390, "height": 800},
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
            print(f"opening tweet: {tweet_url}")
            await page.goto(tweet_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(10)

        except PlaywrightTimeoutError:
            await browser.close()
            return "❌ Could not load the Tweet. Please check the link."

        repliers = set()
        last_count = 0
        idle_rounds = 0
        scroll_pass = 0

        async def find_show_spam_buttons():
            elements = []
            try:
                candidates = await page.query_selector_all('div[role="button"], a, span')
                for el in candidates:
                    text = ""
                    aria = ""

                    try:
                        text = (await el.inner_text() or "").strip().lower()
                    except Exception:
                        pass

                    try:
                        aria = (await el.get_attribute("aria-label") or "").strip().lower()
                    except Exception:
                        pass

                    combined = " ".join([text, aria])

                    if re.search(
                        r"\bshow probable spam\b|\bshow hidden replies\b|\bview hidden replies\b|\bload more replies\b",
                        combined
                    ):
                        elements.append(el)

                return elements
            except Exception:
                return []

        async def safe_click(el):
            try:
                await el.scroll_into_view_if_needed()
                await asyncio.sleep(1)
                await page.evaluate(
                    "(el) => { const r = el.getBoundingClientRect(); window.scrollBy(0, r.top - (window.innerHeight/2)); }",
                    el,
                )
                await asyncio.sleep(0.8)

                try:
                    await el.click(timeout=3000)
                except Exception:
                    await page.evaluate("(e) => e.click()", el)

                await asyncio.sleep(2)
                return True
            except Exception:
                return False

        while scroll_pass < 40:
            scroll_pass += 1
            await page.mouse.wheel(0, 800)
            await asyncio.sleep(2)

            show_spam_buttons = await find_show_spam_buttons()
            for sb in show_spam_buttons:
                ok = await safe_click(sb)
                if ok:
                    print(f"clicked spam/reply expander at pass {scroll_pass}")
                    idle_rounds = 0
                    await asyncio.sleep(2.5)

            try:
                buttons = await page.query_selector_all('div[role="button"], a')
                for b in buttons:
                    try:
                        text = (await b.inner_text() or "").lower()
                    except Exception:
                        text = ""

                    try:
                        html = (await b.inner_html() or "").lower()
                    except Exception:
                        html = ""

                    try:
                        aria = (await b.get_attribute("aria-label") or "").lower()
                    except Exception:
                        aria = ""

                    merged = text + html + aria
                    keywords = [
                        "show more replies",
                        "view hidden replies",
                        "view more replies",
                        "show additional replies",
                        "show replies",
                        "load more replies",
                        "load earlier replies",
                    ]

                    if any(k in merged for k in keywords):
                        await safe_click(b)
            except Exception:
                pass

            for _ in range(5):
                await page.mouse.wheel(0, 500)
                await asyncio.sleep(0.8)

            try:
                username_locators = page.locator(
                    'a[role="link"][href^="/"] span:not([aria-hidden="true"])'
                )
                current_usernames_texts = await username_locators.all_inner_texts()

                for text in current_usernames_texts:
                    if text.startswith("@"):
                        username = text.lstrip("@").lower()
                        if re.fullmatch(r"[a-z0-9_]{1,15}", username):
                            repliers.add(username)

                html_content = await page.content()
                found_html = re.findall(r"@([A-Za-z0-9_]{1,15})", html_content)
                for f in found_html:
                    repliers.add(f.lower())

            except Exception:
                pass

            print(f"scroll {scroll_pass}/40 -> {len(repliers)} usernames found")

            if len(repliers) == last_count:
                idle_rounds += 1
            else:
                idle_rounds = 0
                last_count = len(repliers)

            if idle_rounds >= 3:
                print("no new replies found after 3 idle rounds")
                break

        await context.close()
        await browser.close()

    with open("repliers_list.txt", "w", encoding="utf-8") as f:
        for u in sorted(repliers):
            f.write(f"@{u}\n")

    all_users = [u.lower().lstrip("@") for u in usernames]
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