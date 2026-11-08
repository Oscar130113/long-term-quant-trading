"""
main.py —— 长线价值增强项目 · 主程序调度

改造自短线项目，核心变化：
1. 调度从"每天"改为"每 N 天"（默认 5 天），长线低频。
2. 传递长线参数（预测周期、止盈止损比例、底仓规则）给各模块。
"""

import time
import json
import logging
import sys
import os
import configparser
import colorlog
import datetime

from 决策模块 import 决策模块
from 交易模块 import 交易模块
import 数据更新

for folder in ["运行日志", "数据仓库"]:
    if not os.path.exists(folder):
        os.makedirs(folder)

color_formatter = colorlog.ColoredFormatter(
    "%(log_color)s%(asctime)s - [%(levelname)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={'DEBUG': 'cyan', 'INFO': 'green', 'WARNING': 'yellow', 'ERROR': 'red', 'CRITICAL': 'red,bg_white'}
)
file_formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

console_handler = logging.StreamHandler()
console_handler.setFormatter(color_formatter)
file_handler = logging.FileHandler("运行日志/系统运行日志.log", encoding="utf-8")
file_handler.setFormatter(file_formatter)

logger = logging.getLogger("主系统")
logger.setLevel(logging.INFO)
logger.propagate = False  # 关键：不向根 logger 传播，避免 TUI 劫持根 logger 后重复入队
logger.addHandler(console_handler)
logger.addHandler(file_handler)

配置文件 = "系统配置.properties"


def 读取配置():
    if not os.path.exists(配置文件):
        logger.error(f"找不到配置文件 {配置文件}，系统退出。")
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read(配置文件, encoding='utf-8')

    cfg = {}
    try:
        # 运行与账户
        cfg['tickers'] = [t.strip().upper() for t in config.get('DEFAULT', 'tickers', fallback='TSLA').split(',') if t.strip()]
        cfg['initial_capital'] = config.getfloat('DEFAULT', 'initial_capital', fallback=10000.0)
        cfg['enable_update'] = config.getboolean('DEFAULT', 'enable_update', fallback=True)
        cfg['trade_mode'] = config.get('DEFAULT', 'trade_mode', fallback='local').strip().lower()

        # 调度与预测周期
        cfg['forecast_horizon'] = config.getint('DEFAULT', 'forecast_horizon', fallback=5)
        cfg['run_interval_days'] = config.getint('DEFAULT', 'run_interval_days', fallback=5)

        # 数据与新闻
        cfg['data_start_year'] = config.getint('DEFAULT', 'data_start_year', fallback=2020)
        cfg['news_count'] = config.getint('DEFAULT', 'news_count', fallback=8)
        cfg['news_recent_days'] = config.getint('DEFAULT', 'news_recent_days', fallback=7)
        cfg['news_analyze_limit'] = config.getint('DEFAULT', 'news_analyze_limit', fallback=5)

        # 技术指标
        cfg['ma_window'] = config.getint('DEFAULT', 'ma_window', fallback=20)
        cfg['rsi_window'] = config.getint('DEFAULT', 'rsi_window', fallback=14)
        cfg['macd_fast'] = config.getint('DEFAULT', 'macd_fast', fallback=12)
        cfg['macd_slow'] = config.getint('DEFAULT', 'macd_slow', fallback=26)
        cfg['macd_signal'] = config.getint('DEFAULT', 'macd_signal', fallback=9)
        cfg['qqq_ma_window'] = config.getint('DEFAULT', 'qqq_ma_window', fallback=200)

        # 交易与风控
        cfg['trade_fee_rate'] = config.getfloat('DEFAULT', 'trade_fee_rate', fallback=0.002)
        cfg['stop_loss_pct'] = config.getfloat('DEFAULT', 'stop_loss_pct', fallback=0.02)
        cfg['trailing_take_profit_pct'] = config.getfloat('DEFAULT', 'trailing_take_profit_pct', fallback=0.03)
        cfg['trailing_activate_pct'] = config.getfloat('DEFAULT', 'trailing_activate_pct', fallback=0.02)
        cfg['allow_sell_core'] = config.getboolean('DEFAULT', 'allow_sell_core', fallback=True)
        cfg['position_pct'] = config.getfloat('DEFAULT', 'position_pct', fallback=0.10)

        # 机器学习模型
        cfg['model_n_estimators'] = config.getint('DEFAULT', 'model_n_estimators', fallback=100)
        cfg['model_max_depth'] = config.getint('DEFAULT', 'model_max_depth', fallback=3)
        cfg['model_learning_rate'] = config.getfloat('DEFAULT', 'model_learning_rate', fallback=0.05)
        cfg['model_random_state'] = config.getint('DEFAULT', 'model_random_state', fallback=42)
    except Exception as e:
        logger.error(f"配置文件读取错误: {e}")
        sys.exit(1)
    return cfg


C_BLUE = '\033[94m'
C_CYAN = '\033[96m'
C_YELLOW = '\033[93m'
C_MAGENTA = '\033[95m'
C_RESET = '\033[0m'


def 每日任务():
    start_time = time.time()
    logger.info(f"{C_BLUE}================ 触发长线决策任务 ================{C_RESET}")

    config = 读取配置()
    logger.info(f"  -> 目标股票池: {', '.join(config['tickers'])}")
    logger.info(f"  -> 账户初始资金: ${config['initial_capital']}")
    logger.info(f"  -> 预测周期: {config['forecast_horizon']} 天 | 调度频率: 每 {config['run_interval_days']} 天")
    logger.info("--------------------------------------------------")

    if config['enable_update']:
        logger.info(f"{C_YELLOW}[步骤 1/3]{C_RESET} 拉取行情 + FinGPT 情绪分析...")
        for ticker in config['tickers']:
            try:
                数据更新.更新每日数据(
                    ticker,
                    config['ma_window'], config['rsi_window'],
                    config['macd_fast'], config['macd_slow'], config['macd_signal'],
                    config['forecast_horizon'],
                    config['data_start_year'],
                    config['news_count'], config['news_recent_days'], config['news_analyze_limit']
                )
            except Exception as e:
                logger.error(f"  更新 {ticker} 数据失败: {e}")
    else:
        logger.info(f"{C_YELLOW}[步骤 1/3]{C_RESET} 跳过联网更新，使用本地缓存...")
    logger.info("--------------------------------------------------")

    logger.info(f"{C_YELLOW}[步骤 2/3]{C_RESET} 执行长线策略决策...")
    for ticker in config['tickers']:
        try:
            大脑 = 决策模块(
                ticker,
                config['qqq_ma_window'],
                config['model_n_estimators'],
                config['model_max_depth'],
                config['model_learning_rate'],
                config['forecast_horizon']
            )
            signal = 大脑.生成信号()

            if signal:
                sig_color = '\033[92m' if signal['action'] == "BUY" else '\033[93m'
                logger.info(f"    {C_MAGENTA}[信号]{C_RESET} 预测: {sig_color}{signal['action']}{C_RESET} "
                            f"(未来{config['forecast_horizon']}天上涨概率: {signal['prob_up']*100:.1f}%)")

                操盘手 = 交易模块(
                    ticker,
                    config['initial_capital'],
                    config['trade_fee_rate'],
                    config['stop_loss_pct'],
                    config['trailing_take_profit_pct'],
                    config['allow_sell_core'],
                    config['trade_mode'],
                    config['position_pct'],
                    config['trailing_activate_pct']
                )
                操盘手.执行信号(signal)
            else:
                logger.warning(f"    {ticker} 决策生成失败，本轮跳过交易。")
        except Exception as e:
            logger.error(f"  处理 {ticker} 时发生异常: {e}")
        logger.info("--------------------------------------------------")

    elapsed = time.time() - start_time
    logger.info(f"{C_BLUE}================ 长线决策任务完成 (耗时: {elapsed:.2f} 秒) ================{C_RESET}\n")


def 启动定时器():
    config = 读取配置()
    logger.info(f"长线低频调度已就绪: 每 {config['run_interval_days']} 天运行一次")
    logger.info(f"当前监控股票池: {config['tickers']}")
    logger.info(f"预测周期: {config['forecast_horizon']} 天")
    logger.info(f"交易模式: {config['trade_mode']}")


# ============================================================
# 长任务（daemon）模式：常驻运行 + Alpaca 模拟盘 + 盘中风控巡检
# ============================================================

长任务状态文件 = os.path.join("数据仓库", "长任务状态.json")


def _读长任务状态():
    if os.path.exists(长任务状态文件):
        try:
            with open(长任务状态文件, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _写长任务状态(状态):
    os.makedirs("数据仓库", exist_ok=True)
    with open(长任务状态文件, 'w', encoding='utf-8') as f:
        json.dump(状态, f, ensure_ascii=False, indent=2)


def 盘中风控巡检(config):
    """对股票池逐只做风控巡检（止盈止损），返回触发的动作列表。"""
    触发列表 = []
    for ticker in config['tickers']:
        try:
            操盘手 = 交易模块(
                ticker,
                config['initial_capital'],
                config['trade_fee_rate'],
                config['stop_loss_pct'],
                config['trailing_take_profit_pct'],
                config['allow_sell_core'],
                config['trade_mode'],
                config['position_pct'],
                config['trailing_activate_pct']
            )
            动作 = 操盘手.风控巡检()
            if 动作 in ("止损", "移动止盈"):
                触发列表.append((ticker, 动作))
        except Exception as e:
            logger.error(f"  [巡检] {ticker} 失败: {e}")
    return 触发列表


def 长任务模式(停止标记=None, 风控间隔分钟=15):
    """
    长任务（daemon）模式：
      1. 启动时立即跑一轮完整决策（数据→决策→Alpaca下单）。
      2. 之后常驻运行：
         - 每 风控间隔分钟 做一次盘中风控巡检（拉最新价，触发止盈止损立即卖出）；
         - 距上次决策满 run_interval_days 天时，自动跑下一轮完整决策。
      3. 状态持久化到 数据仓库/长任务状态.json，重启后不会重复决策。

    停止标记: threading.Event，set() 时优雅退出。
    """
    import datetime as _dt

    logger.info(f"{C_BLUE}================ 长任务模式启动 ================{C_RESET}")
    config = 读取配置()
    logger.info(f"  交易模式: {config['trade_mode']} | 股票池: {', '.join(config['tickers'])}")
    logger.info(f"  决策周期: 每 {config['run_interval_days']} 天 | 风控巡检: 每 {风控间隔分钟} 分钟")

    if config['trade_mode'] == 'live':
        logger.error("  ⚠️ 实盘模式未实现，拒绝启动长任务。")
        return

    # 读取上次决策时间（重启不重复决策）
    状态 = _读长任务状态()
    上次决策str = 状态.get("上次决策", "")
    if 上次决策str:
        try:
            上次决策 = _dt.datetime.fromisoformat(上次决策str)
            距今天数 = (_dt.datetime.now() - 上次决策).days
            logger.info(f"  历史状态: 上次决策 {上次决策str}（{距今天数} 天前）")
        except ValueError:
            上次决策 = None
    else:
        上次决策 = None

    # 启动决策（无历史状态 或 已到决策期）
    # ⚠️ 同一天内不重复决策（防止 daemon 重启/状态清空导致重复下单）
    需要启动决策 = False
    if 上次决策 is None:
        需要启动决策 = True
    else:
        距上次 = _dt.datetime.now() - 上次决策
        if 距上次.days >= config['run_interval_days']:
            需要启动决策 = True
        else:
            logger.info(f"  距下次决策还有 {config['run_interval_days'] - 距上次.days} 天，本次启动跳过决策")

    if 需要启动决策:
        每日任务()
        _写长任务状态({"上次决策": _dt.datetime.now().isoformat()})
        logger.info(f"{C_GREEN}  已记录本次决策时间，接下来每 {config['run_interval_days']} 天决策一次。{C_RESET}")

    # ===== 常驻循环 =====
    巡检计数 = 0
    风控间隔分钟 = max(风控间隔分钟, 1)  # 防止 0 导致忙循环
    while True:
        # 可中断的 sleep（每 10 秒醒一次检查停止标记）
        for _ in range(风控间隔分钟 * 6):
            if 停止标记 is not None and 停止标记.is_set():
                logger.info(f"{C_BLUE}================ 长任务模式已停止 ================{C_RESET}")
                return
            time.sleep(10)

        巡检计数 += 1
        now = _dt.datetime.now()

        # 判断是否到决策日
        状态 = _读长任务状态()
        try:
            上次决策 = _dt.datetime.fromisoformat(状态.get("上次决策", ""))
        except ValueError:
            上次决策 = None

        if 上次决策 is None or (now - 上次决策).days >= config['run_interval_days']:
            logger.info(f"{C_YELLOW}[长任务] 到达决策周期（距上次 {(now - 上次决策).days if 上次决策 else '?'} 天），启动新一轮决策...{C_RESET}")
            每日任务()
            _写长任务状态({"上次决策": now.isoformat()})
        else:
            # 非决策时点：盘中风控巡检
            剩余天 = config['run_interval_days'] - (now - 上次决策).days
            logger.info(f"{C_YELLOW}[巡检 #{巡检计数}]{C_RESET} 距下次决策还有 {剩余天} 天，执行盘中风控巡检...")
            触发 = 盘中风控巡检(config)
            if 触发:
                for ticker, 动作 in 触发:
                    logger.info(f"  🔔 [风控触发] {ticker} 触发【{动作}】已卖出")


if __name__ == "__main__":
    logger.info("长线价值增强项目启动...")
    启动定时器()

    # 长线低频：默认手动运行一次，之后可用 cron / 定时任务按天间隔触发
    logger.info("提示：本程序默认立即执行一轮决策（可改由 cron 定时调用）。")
    每日任务()

    logger.info("本轮决策执行完毕，程序退出。")
