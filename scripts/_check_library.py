"""Library tab check — wait longer for zotero."""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
        page.on("console", lambda m: errors.append((m.type, m.text)))
        page.on("requestfailed", lambda r: errors.append(("reqfail", r.url)))

        await page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
        await page.click('button.tab[data-tab="library"]')

        # Wait for table
        await page.wait_for_timeout(5000)
        rows = await page.evaluate("() => document.querySelectorAll('#content table tbody tr').length")
        banner = await page.evaluate("() => document.querySelector('#content .error-box')?.textContent")
        h = await page.evaluate("() => document.querySelector('#content')?.innerHTML?.slice(0, 500)")

        print("library rows:", rows)
        print("banner:", banner)
        print("content:", h)
        print("errors:")
        for e in errors[:10]:
            print(f"  {e}")

        await page.screenshot(path="H:\\SciAssist\\library_screenshot.png", full_page=True)
        print("screenshot: H:\\SciAssist\\library_screenshot.png")

        await browser.close()


asyncio.run(main())