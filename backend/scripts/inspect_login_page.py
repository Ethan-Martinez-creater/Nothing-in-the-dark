"""Capture login-page DOM hints for maintaining MediaCrawler selectors."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    platform = sys.argv[1]
    output_root = Path(sys.argv[2])
    output_root.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        if platform == "bilibili":
            page.goto("https://www.bilibili.com", wait_until="domcontentloaded")
            page.get_by_text("登录", exact=True).first.click()
        elif platform == "weibo":
            page.goto(
                "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog",
                wait_until="domcontentloaded",
            )
        else:
            raise ValueError(f"Unsupported platform: {platform}")
        page.wait_for_timeout(5000)

        elements = page.locator("img, canvas, svg").evaluate_all(
            """elements => elements.map((element, index) => {
              const rect = element.getBoundingClientRect();
              return {
                index,
                tag: element.tagName,
                class: element.getAttribute('class') || '',
                alt: element.getAttribute('alt') || '',
                src: element.getAttribute('src') || '',
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                visible: rect.width > 0 && rect.height > 0,
              };
            })"""
        )
        (output_root / f"{platform}.json").write_text(
            json.dumps(
                {"url": page.url, "title": page.title(), "elements": elements},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        page.screenshot(path=output_root / f"{platform}.png", full_page=True)
        browser.close()


if __name__ == "__main__":
    main()
