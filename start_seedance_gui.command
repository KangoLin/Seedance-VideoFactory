#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "正在执行启动前体检..."
if ! python3 05_Video/scripts/preflight_check.py; then
  echo ""
  read -r -p "按 Enter 关闭窗口..." _
  exit 1
fi
python3 05_Video/scripts/seedance_gui.py
