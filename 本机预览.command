#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo '尚未安装开发环境，请先参考 README 完成依赖安装。'
  read 'reply?按回车关闭。'
  exit 1
fi
.venv/bin/python scripts/start_local.py
