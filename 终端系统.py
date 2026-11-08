"""
终端系统.py —— 长线价值增强项目 · MC 风格常驻终端 TUI

把项目做成"像 Minecraft 服务器控制台"的交互终端：
  - 上半屏：实时滚动的系统日志（后台任务持续输出，自动刷新）。
  - 底部一行：随时输入指令（不用再"清屏→菜单→清屏→菜单"）。

指令一览（输入 help 可随时查看）：
  run          立即跑一轮完整决策（后台执行，日志实时滚动）
  status       查看系统状态 / 配置 / 模式 / 持仓
  mode         切换交易模式（local / alpaca / live）
  pool         查看/修改目标股票池
  news         查看最近新闻与情绪
  screen       选股测试（回测候选池，筛选目标股票池）
  log          查看历史日志文件
  cfg          修改配置文件（打开系统编辑器）
  clear        清空当前屏幕日志
  help         显示指令帮助
  quit / exit  退出系统

启动：./.venv/bin/python 终端系统.py
"""

import os
import sys
import re
import time
import json
import queue
import logging
import threading
import datetime
import configparser
import subprocess

# ===== 颜色 =====
class 色:
    红 = '\033[91m'; 绿 = '\033[92m'; 黄 = '\033[93m'; 蓝 = '\033[94m'
    紫 = '\033[95m'; 青 = '\033[96m'; 白 = '\033[97m'; 灰 = '\033[90m'
    粗 = '\033[1m'; 复位 = '\033[0m'


# ===== 全局状态 =====
日志队列 = queue.Queue()          # 日志渲染队列（(级别, 文本)）
屏幕锁 = threading.Lock()         # 屏幕重绘锁
系统运行中 = threading.Event()    # 后台是否正在跑决策
退出标记 = threading.Event()


# ============================================================
# 日志捕获：把 logging 输出 + print 输出都收进队列，交给 TUI 渲染
# ============================================================

class 队列日志处理器(logging.Handler):
    """把 logging 记录投递到渲染队列。"""
    def emit(self, record):
        try:
            msg = self.format(record)
            级别 = record.levelname
            日志队列.put((级别, msg))
        except Exception:
            pass


def 装日志捕获():
    """劫持根 logger 和 stdout，让所有输出都进入 TUI 日志区。"""
    import logging
    root = logging.getLogger()
    # 移除所有现有 handler，统一走队列
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.INFO)

    # 自定义 Formatter：带颜色级别标记
    class 彩色格式(logging.Formatter):
        def format(self, record):
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            颜色映射 = {
                'DEBUG': 色.灰, 'INFO': 色.绿, 'WARNING': 色.黄,
                'ERROR': 色.红, 'CRITICAL': f'{色.红}{色.粗}',
            }
            c = 颜色映射.get(record.levelname, 色.白)
            return f"{色.灰}{ts}{色.复位} {c}[{record.levelname}]{色.复位} {record.getMessage()}"

    qh = 队列日志处理器()
    qh.setFormatter(彩色格式())
    root.addHandler(qh)

    # 劫持 stdout/stderr（有些模块直接 print）
    class 队列写入器:
        def __init__(self, 级别):
            self.级别 = 级别
            self._缓冲 = ""
        def write(self, s):
            if not s:
                return
            self._缓冲 += s
            if '\n' in self._缓冲:
                lines = self._缓冲.split('\n')
                self._缓冲 = lines.pop()
                for ln in lines:
                    if ln.strip():
                        日志队列.put((self.级别, ln))
            return len(s)
        def flush(self):
            if self._缓冲:
                日志队列.put((self.级别, self._缓冲))
                self._缓冲 = ""
        def isatty(self):
            return False

    sys.stdout = 队列写入器('INFO')
    sys.stderr = 队列写入器('ERROR')


def 直接输出(文本):
    """向日志区直接塞一条消息（绕过 logging）。"""
    日志队列.put(('SYS', 文本))


# ============================================================
# TUI 渲染（MC 控制台风格）
# ============================================================

class 终端:
    def __init__(self):
        self.日志缓冲 = []          # 屏幕保留的日志行（已脱色纯文本存一份用于裁剪）
        self.日志缓冲彩色 = []       # 屏幕保留的日志行（带色）
        self.最大行数 = 1000
        self.输入提示 = "> "
        self.当前输入 = ""
        self.标题 = "长线价值增强项目 · 交易终端"

    # ---- 日志追加 ----
    def 喂日志(self):
        while not 日志队列.empty():
            try:
                级别, 文本 = 日志队列.get_nowait()
            except queue.Empty:
                break
            if 级别 == 'SYS':
                行 = f"{色.青}{文本}{色.复位}"
            else:
                行 = 文本
            脱色 = self._脱色(文本)
            # 去重：与最后一条完全相同则跳过（防止渲染竞态导致重复堆叠）
            if self.日志缓冲 and self.日志缓冲[-1] == 脱色:
                continue
            self.日志缓冲彩色.append(行)
            # 存一份脱色版用于计算显示行数
            self.日志缓冲.append(脱色)
            if len(self.日志缓冲) > self.最大行数:
                裁 = len(self.日志缓冲) - self.最大行数
                self.日志缓冲 = self.日志缓冲[裁:]
                self.日志缓冲彩色 = self.日志缓冲彩色[裁:]

    @staticmethod
    def _脱色(s):
        return re.sub(r'\x1b\[[0-9;]*m', '', s)

    # ---- 计算可视高度 ----
    def _终端尺寸(self):
        try:
            import shutil
            cols, rows = shutil.get_terminal_size((120, 40))
        except Exception:
            cols, rows = 120, 40
        return cols, max(rows, 10)

    # ---- 全屏重绘 ----
    def 重绘(self):
        self.喂日志()
        cols, rows = self._终端尺寸()

        with 屏幕锁:
            out = []
            out.append('\033[?25l')                    # 隐藏光标
            out.append('\033[H')                       # 光标回原点
            out.append('\033[2J')                      # 清屏

            # 顶部标题栏
            标题行 = f" {色.粗}{色.青}{self.标题}{色.复位} "
            if 长任务运行.is_set():
                状态 = f"{色.紫}长任务{色.复位}"
            elif 系统运行中.is_set():
                状态 = f"{色.绿}运行中{色.复位}"
            else:
                状态 = f"{色.灰}待命{色.复位}"
            时间 = datetime.datetime.now().strftime('%H:%M:%S')
            右侧 = f" [{状态} {色.灰}{时间}{色.复位}]"
            填充 = cols - len(self._脱色(标题行)) - len(self._脱色(右侧))
            填充 = max(填充, 0)
            out.append(标题行 + ' ' * 填充 + 右侧 + '\n')

            # 分隔线
            out.append(f"{色.灰}{'─' * cols}{色.复位}\n")

            # 日志区高度 = 总行数 - 顶部2行 - 输入行2行
            日志区高度 = rows - 4
            if 日志区高度 < 3:
                日志区高度 = 3

            # 取最后 N 行日志（按屏幕可见高度裁剪，考虑折行）
            可见 = self._取可见日志(日志区高度, cols)
            for 行 in 可见:
                out.append(行 + '\n')
            # 补空行（保证日志区固定占满，输入行始终在底部）
            已有 = len(可见)
            for _ in range(日志区高度 - 已有):
                out.append('\n')

            # 底部分隔 + 输入行
            out.append(f"{色.灰}{'─' * cols}{色.复位}\n")
            输入显示 = f"{色.黄}→{色.复位} {self.当前输入}"
            out.append(输入显示)
            # 光标定位到输入位置（行 = rows，列 = 输入文字末尾+1）
            out.append(f"\033[{rows};{len(self._脱色(输入显示)) + 1}H")
            out.append('\033[?25h')                    # 显示光标

            sys.__stdout__.write(''.join(out))
            sys.__stdout__.flush()

    def _取可见日志(self, 高度, 宽度):
        """按折行裁剪，返回能在指定高度内显示的日志行（带色）。"""
        if not self.日志缓冲彩色:
            return []
        结果 = []
        用掉 = 0
        # 从最新往前取
        for i in range(len(self.日志缓冲彩色) - 1, -1, -1):
            彩色行 = self.日志缓冲彩色[i]
            纯行 = self.日志缓冲[i]
            占行 = max(1, -(-len(纯行) // max(宽度, 1)))
            if 用掉 + 占行 > 高度:
                break
            结果.append(彩色行)
            用掉 += 占行
            if 用掉 >= 高度:
                break
        结果.reverse()
        return 结果


# ============================================================
# 指令执行
# ============================================================

def 读配置():
    cfg = {}
    if not os.path.exists("系统配置.properties"):
        return cfg
    c = configparser.ConfigParser()
    c.read("系统配置.properties", encoding='utf-8')
    for k in c['DEFAULT']:
        cfg[k] = c['DEFAULT'][k]
    return cfg


def 指令_status():
    直接输出(f"{色.粗}── 系统状态 ──{色.复位}")
    cfg = 读配置()
    直接输出(f"  股票池   : {cfg.get('tickers', 'TSLA')}")
    直接输出(f"  交易模式 : {cfg.get('trade_mode', 'local')}")
    直接输出(f"  预测周期 : {cfg.get('forecast_horizon', '5')} 天")
    直接输出(f"  调度频率 : 每 {cfg.get('run_interval_days', '5')} 天")
    直接输出(f"  初始资金 : ${cfg.get('initial_capital', '10000')}")
    止损 = float(cfg.get('stop_loss_pct', '0.02')) * 100
    止盈 = float(cfg.get('trailing_take_profit_pct', '0.03')) * 100
    直接输出(f"  止损/止盈 : -{止损:.0f}% / 回撤{止盈:.0f}%")
    # Ollama 在线检测
    try:
        import requests
        s = requests.Session(); s.trust_env = False
        r = s.get("http://localhost:11434/api/version", timeout=3)
        ollama = "在线" if r.status_code == 200 else "离线"
    except Exception:
        ollama = "离线"
    直接输出(f"  Ollama   : {ollama}")


def 指令_run():
    if 系统运行中.is_set():
        直接输出(f"{色.黄}[系统] 已有任务在运行，请稍候...{色.复位}")
        return
    def 工作():
        系统运行中.set()
        try:
            主程序 = _导入main()
            主程序.每日任务()
        except Exception as e:
            logging.getLogger().error(f"决策任务异常: {e}")
        finally:
            系统运行中.clear()
    t = threading.Thread(target=工作, daemon=True)
    t.start()
    直接输出(f"{色.绿}[系统] 已启动后台决策任务，日志实时滚动中...{色.复位}")


def _导入main():
    """
    导入 main 模块，并把它的日志输出正确接进 TUI：
    - 摘掉它自己的 console_handler（写终端，会破坏 TUI 屏幕 / 重复入队）；
    - 挂上一个队列日志处理器，让 main 的日志走 TUI 队列（不丢失、不重复）。
    """
    import main as 主程序

    # 摘掉「控制台输出」类 handler：即 StreamHandler 但不是 FileHandler 的。
    # （FileHandler 是 StreamHandler 的子类，需排除，保留它写文件归档）
    要保留 = []
    for h in list(主程序.logger.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            continue  # 控制台 handler，摘掉
        要保留.append(h)
    主程序.logger.handlers[:] = 要保留

    # 挂上队列处理器（若还没有），让 main 日志进 TUI 渲染队列
    if not any(isinstance(h, 队列日志处理器) for h in 主程序.logger.handlers):
        qh = 队列日志处理器()
        qh.setFormatter(logging.Formatter("%(message)s"))
        主程序.logger.addHandler(qh)

    return 主程序


# ===== 长任务（daemon）模式 =====
长任务运行 = threading.Event()     # 标记：长任务正在跑（标题栏显示用）
长任务停止 = threading.Event()      # 通知：长任务优雅退出


def 指令_daemon(参数=None):
    参数 = (参数 or 'start').strip().lower()
    if 参数 in ('start', '启动', '1'):
        if 长任务运行.is_set():
            直接输出(f"{色.黄}[长任务] 已在运行中，无需重复启动。{色.复位}")
            return
        cfg = 读配置()
        模式 = cfg.get('trade_mode', 'local')
        if 模式 == 'live':
            直接输出(f"{色.红}[长任务] 实盘模式未实现，拒绝启动。{色.复位}")
            return
        间隔 = int(cfg.get('patrol_interval_minutes', '15'))

        def 工作():
            长任务运行.set()
            长任务停止.clear()
            try:
                主程序 = _导入main()
                主程序.长任务模式(停止标记=长任务停止, 风控间隔分钟=间隔)
            except Exception as e:
                logging.getLogger().error(f"长任务异常退出: {e}")
            finally:
                长任务运行.clear()

        t = threading.Thread(target=工作, daemon=True)
        t.start()
        直接输出(f"{色.绿}[长任务] 已启动：常驻运行 | 模式={模式} | 每{间隔}分钟风控巡检{色.复位}")
        直接输出(f"{色.灰}         行为：启动即跑一轮决策（Alpaca模拟盘真实下单），{色.复位}")
        直接输出(f"{色.灰}         之后每隔 {cfg.get('run_interval_days','5')} 天自动决策，中间持续止盈止损巡检。{色.复位}")
        直接输出(f"{色.灰}         停止：daemon stop{色.复位}")
    elif 参数 in ('stop', '停止', '0'):
        if not 长任务运行.is_set():
            直接输出(f"{色.黄}[长任务] 当前未在运行。{色.复位}")
            return
        长任务停止.set()
        直接输出(f"{色.绿}[长任务] 已发出停止信号，正在优雅退出（最多等 {间隔_文本()}）...{色.复位}")
    elif 参数 in ('status', '状态'):
        if 长任务运行.is_set():
            直接输出(f"{色.绿}[长任务] 运行中{色.复位}")
            try:
                import json as _json
                with open(os.path.join('数据仓库', '长任务状态.json'), encoding='utf-8') as f:
                    状态 = _json.load(f)
                直接输出(f"  上次决策: {状态.get('上次决策', '?')}")
            except Exception:
                直接输出(f"  （状态文件读取失败或尚未决策）")
        else:
            直接输出(f"{色.灰}[长任务] 未运行{色.复位}")
    else:
        直接输出(f"{色.粗}── 长任务模式 ──{色.复位}")
        直接输出(f"  daemon start   启动（常驻运行，Alpaca 模拟盘交易+风控巡检）")
        直接输出(f"  daemon stop    停止")
        直接输出(f"  daemon status  查看状态")


def 间隔_文本():
    return "10 秒"


def 指令_mode(参数=None):
    cfg = 读配置()
    if 参数:
        映射 = {'local': 'local', 'alpaca': 'alpaca', 'live': 'live', '1': 'local', '2': 'alpaca', '3': 'live'}
        if 参数 in 映射:
            新模式 = 映射[参数]
            _写配置键('trade_mode', 新模式)
            直接输出(f"{色.绿}✅ 交易模式已切换为 {新模式}{色.复位}")
        else:
            直接输出(f"{色.红}无效模式，可选 local / alpaca / live{色.复位}")
        return
    直接输出(f"{色.粗}── 切换交易模式 ──{色.复位}")
    直接输出(f"  当前模式: {cfg.get('trade_mode', 'local')}")
    直接输出(f"  {色.绿}local{色.复位}   本地记账（最安全）")
    直接输出(f"  {色.绿}alpaca{色.复位}  Alpaca 模拟盘真实下单")
    直接输出(f"  {色.红}live{色.复位}    实盘（未实现，禁用）")
    直接输出(f"  用法: mode <local|alpaca|live>")


def 指令_pool(参数=None):
    cfg = 读配置()
    if 参数:
        新池 = ','.join([s.strip().upper() for s in 参数.split(',') if s.strip()])
        if 新池:
            _写配置键('tickers', 新池)
            直接输出(f"{色.绿}✅ 目标股票池已更新: {新池}{色.复位}")
        return
    直接输出(f"{色.粗}── 目标股票池 ──{色.复位}")
    直接输出(f"  当前: {cfg.get('tickers', 'TSLA')}")
    直接输出(f"  用法: pool TSLA,NVDA,AAPL  (逗号分隔，覆盖更新)")


def 指令_news():
    直接输出(f"{色.粗}── 拉取最近新闻 ──{色.复位}")
    cfg = 读配置()
    tickers = [t.strip().upper() for t in cfg.get('tickers', 'TSLA').split(',') if t.strip()]
    def 工作():
        try:
            import 新闻源
            for t in tickers:
                新闻 = 新闻源.拉取新闻(t, 条数=int(cfg.get('news_count', '8')), 最近天数=int(cfg.get('news_recent_days', '7')))
                直接输出(f"{色.青}── {t} 最近新闻（{len(新闻)}条）──{色.复位}")
                for n in 新闻[:5]:
                    直接输出(f"  [{n['时间'][:10]}] {n['标题'][:60]}")
        except Exception as e:
            直接输出(f"{色.红}拉取新闻失败: {e}{色.复位}")
    threading.Thread(target=工作, daemon=True).start()


def 指令_screen():
    if 系统运行中.is_set():
        直接输出(f"{色.黄}[系统] 已有任务在运行，请稍候...{色.复位}")
        return
    cfg = 读配置()
    候选池 = cfg.get('candidate_pool', 'TSLA,NVDA,AAPL,MSFT,META,GOOGL')
    测试天数 = int(cfg.get('backtest_days', '100'))
    预测周期 = int(cfg.get('forecast_horizon', '5'))
    def 工作():
        系统运行中.set()
        try:
            直接输出(f"{色.粗}── 选股回测（未来{预测周期}天 / 最近{测试天数}天）──{色.复位}")
            直接输出(f"  候选池: {候选池}")
            import 选股测试 as 选股
            池 = [s.strip().upper() for s in 候选池.split(',') if s.strip()]
            结果 = 选股.选股测试(池, 测试天数=测试天数, 预测周期=预测周期)
            if 结果:
                建议 = [r['股票'] for r in 结果 if r.get('超额', 0) > 5]
                if 建议:
                    直接输出(f"{色.绿}✅ 建议纳入股票池: {', '.join(建议)}{色.复位}")
                    直接输出(f"   输入 pool {','.join(建议)} 可写入股票池")
        except Exception as e:
            直接输出(f"{色.红}选股测试失败: {e}{色.复位}")
        finally:
            系统运行中.clear()
    threading.Thread(target=工作, daemon=True).start()


def 指令_log():
    直接输出(f"{色.粗}── 历史日志文件 ──{色.复位}")
    目录 = "运行日志"
    if not os.path.exists(目录):
        直接输出(f"{色.灰}暂无日志。{色.复位}")
        return
    files = sorted(os.listdir(目录))
    if not files:
        直接输出(f"{色.灰}暂无日志。{色.复位}")
        return
    for f in files:
        直接输出(f"  {f}")


def 指令_cfg():
    直接输出(f"{色.黄}正在用系统编辑器打开配置文件...{色.复位}")
    try:
        subprocess.Popen(['open', '系统配置.properties'])
    except Exception as e:
        直接输出(f"{色.红}打开失败: {e}{色.复位}")


def 指令_help():
    直接输出(f"{色.粗}── 指令帮助 ──{色.复位}")
    指令表 = [
        ("run",         "立即跑一轮完整决策（后台，日志实时滚动）"),
        ("daemon",      "长任务模式：常驻运行+定期决策+盘中止盈止损巡检（daemon start/stop/status）"),
        ("status",      "查看系统状态 / 配置 / 模式 / 持仓"),
        ("mode [x]",    "切换交易模式（local/alpaca/live），不带参数查看"),
        ("pool [股池]",  "查看/修改目标股票池（逗号分隔）"),
        ("news",        "查看最近新闻"),
        ("screen",      "选股测试（回测候选池，筛选目标股票池）"),
        ("log",         "查看历史日志文件"),
        ("cfg",         "修改配置文件"),
        ("clear",       "清空屏幕日志"),
        ("help",        "显示本帮助"),
        ("quit/exit",   "退出系统"),
    ]
    for cmd, desc in 指令表:
        直接输出(f"  {色.绿}{cmd:<14}{色.复位} {desc}")


def _写配置键(键, 值):
    文件 = "系统配置.properties"
    with open(文件, 'r', encoding='utf-8') as f:
        内容 = f.read()
    内容 = re.sub(rf'^{键}=.*$', f'{键}={值}', 内容, flags=re.MULTILINE)
    with open(文件, 'w', encoding='utf-8') as f:
        f.write(内容)


def 执行指令(原始):
    原始 = 原始.strip()
    if not 原始:
        return True
    部分 = 原始.split(None, 1)
    指令 = 部分[0].lower()
    参数 = 部分[1] if len(部分) > 1 else None

    if 指令 in ('quit', 'exit', 'q'):
        直接输出(f"{色.黄}[系统] 再见！{色.复位}")
        return False
    elif 指令 == 'help':
        指令_help()
    elif 指令 == 'run':
        指令_run()
    elif 指令 == 'daemon':
        指令_daemon(参数)
    elif 指令 == 'status':
        指令_status()
    elif 指令 == 'mode':
        指令_mode(参数)
    elif 指令 == 'pool':
        指令_pool(参数)
    elif 指令 == 'news':
        指令_news()
    elif 指令 == 'screen':
        指令_screen()
    elif 指令 == 'log':
        指令_log()
    elif 指令 == 'cfg':
        指令_cfg()
    elif 指令 == 'clear':
        全局终端.日志缓冲.clear()
        全局终端.日志缓冲彩色.clear()
    elif 指令 == '':
        pass
    else:
        直接输出(f"{色.红}未知指令: {原始}（输入 help 查看帮助）{色.复位}")
    return True


# ============================================================
# 主循环
# ============================================================

全局终端 = 终端()


def 渲染线程():
    """每 100ms 重绘一次屏幕。"""
    while not 退出标记.is_set():
        全局终端.重绘()
        time.sleep(0.1)


def 主循环():
    # 开启原始模式读单字符（用于输入回显与退格）
    import tty
    import termios

    # 欢迎信息
    直接输出(f"{色.青}{色.粗}欢迎进入长线价值增强项目交易终端{色.复位}")
    直接输出(f"{色.灰}输入 help 查看所有指令。{色.复位}")
    直接输出("")

    渲染 = threading.Thread(target=渲染线程, daemon=True)
    渲染.start()

    fd = sys.stdin.fileno()
    旧设置 = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while not 退出标记.is_set():
            ch = os.read(fd, 1)
            if not ch:
                continue
            # 处理 UTF-8 多字节（中文输入）
            try:
                if ch[0] >= 0x80:
                    # 读后续字节
                    剩余 = 0
                    b = ch[0]
                    if b & 0xE0 == 0xC0: 剩余 = 1
                    elif b & 0xF0 == 0xE0: 剩余 = 2
                    elif b & 0xF8 == 0xF0: 剩余 = 3
                    更多 = os.read(fd, 剩余)
                    ch = (ch + 更多).decode('utf-8')
                else:
                    ch = ch.decode('utf-8')
            except Exception:
                continue

            if ch in ('\r', '\n'):
                命令 = 全局终端.当前输入
                全局终端.当前输入 = ""
                # 回显命令到日志
                直接输出(f"{色.黄}→{色.复位} {命令}")
                if not 执行指令(命令):
                    退出标记.set()
                    break
            elif ch in ('\x7f', '\b'):
                全局终端.当前输入 = 全局终端.当前输入[:-1]
            elif ch == '\x03':   # Ctrl+C
                退出标记.set()
                break
            elif ch == '\t':     # Tab 忽略
                continue
            elif ord(ch) >= 32:
                全局终端.当前输入 += ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, 旧设置)
        # 恢复光标、清屏
        with 屏幕锁:
            sys.__stdout__.write('\033[?25h\033[H\033[2J')
            sys.__stdout__.flush()


if __name__ == "__main__":
    # 单实例锁：防止多个终端系统同时运行导致日志互相干扰/重复刷屏
    锁文件 = os.path.join("数据仓库", ".终端系统.lock")
    import fcntl
    锁句柄 = None
    try:
        锁句柄 = open(锁文件, 'w')
        fcntl.flock(锁句柄, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        print(f"{色.红}⚠️ 已有另一个终端系统实例在运行，请先关闭它再启动。{色.复位}")
        sys.exit(1)

    try:
        装日志捕获()
        主循环()
    except KeyboardInterrupt:
        pass
    finally:
        退出标记.set()
        with 屏幕锁:
            sys.__stdout__.write('\033[?25h')
            sys.__stdout__.flush()
        if 锁句柄:
            try:
                fcntl.flock(锁句柄, fcntl.LOCK_UN)
                锁句柄.close()
            except Exception:
                pass
            try:
                os.remove(锁文件)
            except Exception:
                pass
        print(f"\n{色.绿}已退出终端系统。{色.复位}")
