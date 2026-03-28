#!/usr/bin/env bash
set -e
pip install aiogram==3.26.0 playwright==1.58.0 requests==2.33.0
python -c "from playwright.sync_api import sync_playwright; print('playwright ok')"
PLAYWRIGHT_BROWSERS_PATH=/opt/render/.cache/ms-playwright python -m playwright install chromium
