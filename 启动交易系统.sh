#!/bin/bash
# ============================================================
# 一键启动长线交易系统（开机自启 + 立即开始交易）
# ============================================================
# 双击此文件，或在终端里执行：bash 启动交易系统.sh
#
# 它会：
#   1. 注册 launchd 开机自启服务（崩溃自动重启）
#   2. 立即启动长任务守护进程（开始决策+下单+巡检）
#
# 之后整周（甚至整月）不用管，系统自己跑。
# ============================================================

set -e

项目目录="/Users/a1234/Desktop/Oscar/项目/股票/长线项目"
PLIST="$HOME/Library/LaunchAgents/com.oscar.changxian-trading.plist"
服务名="com.oscar.changxian-trading"

cd "$项目目录"

echo "================================================"
echo "  长线价值增强项目 · 一键启动"
echo "================================================"
echo ""

# 1. 停掉可能已在跑的旧服务
echo "[1/3] 清理旧服务..."
launchctl bootout gui/$(id -u)/$服务名 2>/dev/null || true

# 2. 加载 launchd 服务（开机自启 + 崩溃重启）
echo "[2/3] 注册开机自启服务..."
launchctl bootstrap gui/$(id -u) "$PLIST" 2>/dev/null || launchctl load -w "$PLIST" 2>/dev/null || {
    echo "  ⚠️ launchd 加载失败，改用直接启动模式"
    echo "  （这种模式下关终端/重启电脑会停，需重新跑此脚本）"
    echo ""
    echo "[3/3] 直接启动长任务守护进程..."
    exec "$项目目录/.venv/bin/python" "$项目目录/长任务.py"
}

# 3. 确认服务状态
sleep 2
echo "[3/3] 检查服务状态..."
if launchctl list | grep -q "$服务名"; then
    echo "  ✅ 开机自启服务已注册并启动"
    echo ""
    echo "================================================"
    echo "  完成！系统已常驻运行"
    echo "================================================"
    echo ""
    echo "  · 现在已开始第一轮决策（拉行情→AI情绪→决策→Alpaca下单）"
    echo "  · 之后每 15 分钟盘中止盈止损巡检"
    echo "  · 每 5 天自动下一轮决策"
    echo "  · 崩溃会自动重启，开机自动启动"
    echo ""
    echo "  查看实时日志："
    echo "    tail -f \"$项目目录/运行日志/长任务.log\""
    echo ""
    echo "  手动停止："
    echo "    launchctl bootout gui/$(id -u)/$服务名"
    echo ""
else
    echo "  ⚠️ 服务未检测到，回退到直接启动模式"
    exec "$项目目录/.venv/bin/python" "$项目目录/长任务.py"
fi
