#!/bin/bash
# 一键安装开机自启服务（长线交易守护进程）
# 用法：bash 安装定时任务.sh

set -e
PLIST="$HOME/Library/LaunchAgents/com.oscar.changxian-trading.plist"

# 1. 先停掉可能已在跑的旧服务
launchctl bootout gui/$(id -u)/com.oscar.changxian-trading 2>/dev/null || true

# 2. 加载服务
launchctl bootstrap gui/$(id -u) "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"

# 3. 确认状态
sleep 2
echo "=== 服务状态 ==="
launchctl list | grep -i changxian && echo "✅ 已安装并启动" || echo "⚠️ 未检测到服务，请手动检查"
echo ""
echo "已安装开机自启服务。下次开机自动运行长任务守护进程。"
echo "手动启停命令："
echo "  停止: launchctl bootout gui/\$(id -u)/com.oscar.changxian-trading"
echo "  启动: launchctl bootstrap gui/\$(id -u) $PLIST"
echo "  日志: tail -f 运行日志/长任务.log"
