"""
长任务.py —— 长线价值增强项目 · 常驻交易守护进程（简单可靠版）

替代之前的终端系统 TUI（那套交互界面刷屏问题太多，已弃用）。
这是一个纯命令行守护进程：
  - 启动立即跑一轮完整决策（数据→决策→Alpaca 模拟盘下单）
  - 之后常驻循环：
      · 每 patrol_interval_minutes 分钟拉最新价做盘中风控巡检（止盈止损）
      · 每 run_interval_days 天自动跑下一轮完整决策
  - 所有日志同时输出到终端和 运行日志/长任务.log

用法：
  ./.venv/bin/python 长任务.py            # 前台运行（推荐，Ctrl+C 停止）
  nohup ./.venv/bin/python 长任务.py &    # 后台运行

特点：
  · 单文件、单线程、无渲染线程、无锁竞争 —— 不可能再出现刷屏。
  · 决策去重：同一天内不重复决策（重启也不重复）。
  · 状态持久化到 数据仓库/长任务状态.json。
"""

import os
import sys
import time
import json
import datetime
import logging
import configparser
import signal

# ===== 日志：同时输出到终端 + 文件 =====
os.makedirs("运行日志", exist_ok=True)
os.makedirs("数据仓库", exist_ok=True)

logger = logging.getLogger("长任务")
logger.setLevel(logging.INFO)
logger.propagate = False

# 终端 handler（简洁格式）
终端格式 = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_终端 = logging.StreamHandler(sys.stdout)
_终端.setFormatter(终端格式)
logger.addHandler(_终端)

# 文件 handler（完整格式）
文件格式 = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_文件 = logging.FileHandler("运行日志/长任务.log", encoding="utf-8")
_文件.setFormatter(文件格式)
logger.addHandler(_文件)

配置文件 = "系统配置.properties"
状态文件 = os.path.join("数据仓库", "长任务状态.json")
PID文件 = os.path.join("数据仓库", ".长任务.pid")  # 供定时启动器检测存活用


def 写PID():
    """把自己的 PID 写到锁文件，供定时启动器检测存活。"""
    try:
        with open(PID文件, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def 清PID():
    try:
        if os.path.exists(PID文件):
            os.remove(PID文件)
    except Exception:
        pass


def 读取配置():
    """读取系统配置，返回字典。"""
    if not os.path.exists(配置文件):
        logger.error("找不到配置文件，退出。")
        sys.exit(1)
    c = configparser.ConfigParser()
    c.read(配置文件, encoding="utf-8")
    cfg = {}
    for k in c["DEFAULT"]:
        cfg[k] = c["DEFAULT"][k]
    return cfg


def 读状态():
    """读长任务状态（上次决策时间）。"""
    if os.path.exists(状态文件):
        try:
            with open(状态文件, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def 写状态(状态):
    with open(状态文件, "w", encoding="utf-8") as f:
        json.dump(状态, f, ensure_ascii=False, indent=2)


def 跑一轮决策():
    """调用 main 的每日任务（数据→决策→交易）。"""
    import main
    main.每日任务()


def 盘中巡检(cfg):
    """对股票池逐只做风控巡检，返回触发动作列表。"""
    from 交易模块 import 交易模块
    触发 = []
    tickers = [t.strip().upper() for t in cfg.get("tickers", "TSLA").split(",") if t.strip()]
    for t in tickers:
        try:
            操盘手 = 交易模块(
                t,
                float(cfg.get("initial_capital", "10000")),
                float(cfg.get("trade_fee_rate", "0.002")),
                float(cfg.get("stop_loss_pct", "0.02")),
                float(cfg.get("trailing_take_profit_pct", "0.03")),
                cfg.get("allow_sell_core", "true") == "true",
                cfg.get("trade_mode", "alpaca"),
                float(cfg.get("position_pct", "0.10")),
                float(cfg.get("trailing_activate_pct", "0.02")),
            )
            动作 = 操盘手.风控巡检()
            if 动作 in ("止损", "移动止盈"):
                触发.append((t, 动作))
        except Exception as e:
            logger.error(f"[巡检] {t} 失败: {e}")
    return 触发


def 是否已交易过(cfg):
    """
    检查 Alpaca 是否已有今天的挂单或持仓。
    防止重复决策下单（比如自动化任务重启时不应再次下单）。
    local 模式下检查本地账本是否有持仓。
    """
    模式 = cfg.get("trade_mode", "alpaca")
    if 模式 == "alpaca":
        try:
            import requests
            from dotenv import load_dotenv
            load_dotenv()
            h = {
                "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY_ID"),
                "APCA-API-SECRET-KEY": os.getenv("ALPACA_API_SECRET_KEY"),
            }
            s = requests.Session()
            s.trust_env = False
            b = os.getenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2")
            挂单 = s.get(f"{b}/orders?status=open", headers=h, timeout=10).json()
            持仓 = s.get(f"{b}/positions", headers=h, timeout=10).json()
            if 挂单 or 持仓:
                return True
        except Exception as e:
            logger.warning(f"检查 Alpaca 状态失败({e})，假设未交易过")
    else:
        # local 模式：检查本地账本
        for t in cfg.get("tickers", "TSLA").split(","):
            t = t.strip().upper()
            账本 = os.path.join("数据仓库", t, f"{t}_账户账本.json")
            if os.path.exists(账本):
                try:
                    with open(账本) as f:
                        p = json.load(f)
                    if p.get("shares", 0) > 0:
                        return True
                except Exception:
                    pass
    return False


def 是否到决策期(cfg, 上次决策):
    """判断是否该跑新一轮决策。"""
    if not 上次决策:
        return True
    try:
        上次 = datetime.datetime.fromisoformat(上次决策)
    except (ValueError, TypeError):
        return True
    间隔天 = int(cfg.get("run_interval_days", "5"))
    return (datetime.datetime.now() - 上次).days >= 间隔天


def 主循环():
    logger.info("=" * 50)
    logger.info("长线价值增强项目 · 常驻交易守护进程 启动")
    logger.info("=" * 50)

    cfg = 读取配置()
    模式 = cfg.get("trade_mode", "alpaca")
    tickers = cfg.get("tickers", "TSLA")
    巡检分钟 = max(int(cfg.get("patrol_interval_minutes", "15")), 1)
    决策天 = int(cfg.get("run_interval_days", "5"))

    if 模式 == "live":
        logger.error("实盘模式未实现，拒绝启动。")
        return

    logger.info(f"交易模式: {模式} | 股票池: {tickers}")
    logger.info(f"决策周期: 每 {决策天} 天 | 风控巡检: 每 {巡检分钟} 分钟")
    logger.info("-" * 50)

    # 启动决策
    状态 = 读状态()
    上次决策 = 状态.get("上次决策", "")
    if 是否到决策期(cfg, 上次决策):
        if 是否已交易过(cfg):
            logger.info("检测到已有挂单或持仓，跳过本次决策（防止重复下单）。")
        else:
            if 上次决策:
                logger.info(f"距上次决策已超过 {决策天} 天，开始新决策...")
            else:
                logger.info("首次启动，执行初始决策...")
            跑一轮决策()
            写状态({"上次决策": datetime.datetime.now().isoformat()})
            logger.info("初始决策完成，进入常驻循环。")
    else:
        上次 = datetime.datetime.fromisoformat(上次决策)
        剩余 = 决策天 - (datetime.datetime.now() - 上次).days
        logger.info(f"距下次决策还有 {剩余} 天，本次启动跳过决策，直接进入常驻循环。")

    logger.info("-" * 50)
    logger.info("常驻运行中，按 Ctrl+C 停止。")
    logger.info("-" * 50)

    # ===== 常驻循环 =====
    巡检计数 = 0
    while True:
        # 分段 sleep（每 10 秒醒一次，便于响应 Ctrl+C）
        for _ in range(巡检分钟 * 6):
            time.sleep(10)

        try:
            cfg = 读取配置()
        except Exception:
            continue

        巡检计数 += 1
        now = datetime.datetime.now()
        状态 = 读状态()
        上次决策 = 状态.get("上次决策", "")

        if 是否到决策期(cfg, 上次决策):
            # 到达决策周期：跑完整决策
            logger.info(f"[决策] 到达决策周期，启动新一轮决策...")
            跑一轮决策()
            写状态({"上次决策": now.isoformat()})
        else:
            # 未到决策：盘中风控巡检
            上次 = datetime.datetime.fromisoformat(上次决策)
            剩余 = 决策天 - (now - 上次).days
            logger.info(f"[巡检 #{巡检计数}] 距下次决策 {剩余} 天，执行盘中风控巡检...")
            触发 = 盘中巡检(cfg)
            if 触发:
                for t, 动作 in 触发:
                    logger.info(f"  🔔 [风控触发] {t} 触发【{动作}】已卖出")
            else:
                logger.info(f"  无触发。")


if __name__ == "__main__":
    # Ctrl+C 优雅退出
    def _退出(信号, 帧):
        logger.info("\n收到停止信号，长任务已停止。")
        清PID()
        sys.exit(0)

    signal.signal(signal.SIGINT, _退出)
    signal.signal(signal.SIGTERM, _退出)

    写PID()  # 记录自己 PID，供定时启动器检测
    try:
        主循环()
    except KeyboardInterrupt:
        logger.info("\n已手动停止。")
    finally:
        清PID()
