#!/bin/bash
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
echo "启动斑马鱼多模型多线程智能体系统..."
python3 -m cli.main
