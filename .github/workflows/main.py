name: IPTV Traffic Speedtest

on:
  schedule:
    - cron: '0 16 * * *' # 每天北京时间凌晨0点运行
  workflow_dispatch:      # 允许手动点击运行

jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/checkout@v4
        with:
          python-version: '3.10'

      - name: Install Dependencies
        run: pip install requests urllib3

      - name: Run Speedtest
        run: python jar/traffic_test.py

      - name: Commit and Push Report
        run: |
          git config --local user.email "github-actions[bot]@users.noreply.github.com"
          git config --local user.name "github-actions[bot]"
          git add jar/traffic_report.txt jar/traffic_summary.json
          git commit -m "📊 Update traffic report [$(date '+%Y-%m-%d %H:%M')]" || exit 0
          git push
