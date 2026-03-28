#!/usr/bin/env bash
set -e
pip install aiogram==3.26.0 playwright==1.58.0 requests==2.33.0
python -m playwright install
