"""Playwright check for all tabs."""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
        page.on("console", lambda m: errors.append((m.type, m.text)))
        page.on("requestfailed", lambda r: errors.append(("reqfail", r.url + " " + str(r.failure))))

        await page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
        await page.wait_for_timeout(500)

        for tab in ["ask", "search", "graph", "library", "logs"]:
            print(f"\n=== Tab: {tab} ===")
            errors.clear()
            try:
                await page.click(f'button.tab[data-tab="{tab}"]', timeout=3000)
                await page.wait_for_timeout(1500)
            except Exception as e:
                print(f"  click error: {e}")
            content = await page.evaluate("() => document.getElementById('content').innerHTML.slice(0, 200)")
            content = content.replace("\n", " ")[:200]
            print(f"  content: {content}")
            for e in errors[:10]:
                print(f"  {e[0]}: {e[1][:200]}")

        await browser.close()


asyncio.run(main())