#!/usr/bin/env bash
set -e
pip install aiogram==3.26.0 playwright==1.58.0 requests==2.33.0
python -c "
import subprocess, sys
result = subprocess.run([sys.executable, '-m', 'playwright', 'install', '--help'], capture_output=True, text=True)
print(result.stdout)
print(result.stderr)
"
python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
    browser.close()
    print('chromium works!')
" || python -m playwright install chromium-headless-shell || python -m playwright install chrome
