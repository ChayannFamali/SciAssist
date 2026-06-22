"""Playwright check: Process tab works."""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
        page.on("console", lambda m: errors.append((m.type, m.text)))

        await page.goto("http://127.0.0.1:8775/", wait_until="networkidle")
        await page.wait_for_timeout(500)

        # Click Process tab
        await page.click('button.tab[data-tab="process"]')
        await page.wait_for_timeout(1500)

        # Check that 4 cards rendered
        cards = await page.evaluate("() => document.querySelectorAll('.process-card').length")
        print("process cards:", cards)

        # Check submit button
        btn = await page.evaluate("""
            () => {
                const btn = document.querySelector('button[type=button]');
                return btn ? btn.textContent : null;
            }
        """)
        print("first button:", btn)

        # Submit a quick job
        await page.fill('#in-gaps', 'transformer attention')
        await page.click('button:has-text("Найти gaps")')
        await page.wait_for_timeout(2000)

        # Check jobs list
        rows = await page.evaluate("() => document.querySelectorAll('#jobs-box tbody tr').length")
        print("jobs rows:", rows)

        # Check first row status
        status = await page.evaluate("""
            () => {
                const span = document.querySelector('.status-badge');
                return span ? span.textContent : null;
            }
        """)
        print("first job status:", status)

        # Take screenshot
        await page.screenshot(path="H:\\SciAssist\\process_screenshot.png")
        print("screenshot: H:\\SciAssist\\process_screenshot.png")

        for e in errors[:10]:
            print(f"  {e[0]}: {e[1][:150]}")

        await browser.close()


asyncio.run(main())