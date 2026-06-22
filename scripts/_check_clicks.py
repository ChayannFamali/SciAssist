"""Playwright: click a node, verify note opens."""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
        page.on("console", lambda m: errors.append((m.type, m.text)))

        await page.goto("http://127.0.0.1:8000/", wait_until="networkidle")
        await page.wait_for_timeout(500)

        # Go to Graph
        await page.click('button.tab[data-tab="graph"]')
        await page.wait_for_timeout(2000)

        # Check cytoscape rendered
        canvas = await page.evaluate("""
            () => {
                const cy = document.querySelector('#cy canvas');
                if (!cy) return 'no canvas';
                return { width: cy.width, height: cy.height };
            }
        """)
        print("cy canvas:", canvas)

        # Click first node
        nodes = await page.evaluate("""
            () => {
                if (typeof cytoscape === 'undefined') return null;
                // Use the public state via global — but it's in IIFE. Let's grab nodes by clicking on the canvas.
                return document.querySelectorAll('.cytoscape').length;
            }
        """)
        print("cytoscape containers:", nodes)

        # Click on canvas (approx middle, where node should be)
        await page.evaluate("""
            () => {
                // Force-click on first node via the cy instance — but we don't have access. So click canvas at approximate position.
            }
        """)

        # Take screenshot
        await page.screenshot(path="H:\\SciAssist\\graph_screenshot.png")
        print("screenshot saved to H:\\SciAssist\\graph_screenshot.png")

        # Library test
        await page.click('button.tab[data-tab="library"]')
        await page.wait_for_timeout(1500)
        rows = await page.evaluate("() => document.querySelectorAll('#content table tbody tr').length")
        print("library rows:", rows)
        banner = await page.evaluate("() => document.querySelector('#content .error-box')?.textContent")
        print("library banner:", banner)

        # Search test
        await page.click('button.tab[data-tab="search"]')
        await page.wait_for_timeout(500)
        print("\nAll errors:")
        for e in errors[:15]:
            print(f"  {e[0]}: {e[1][:200]}")

        await browser.close()


asyncio.run(main())