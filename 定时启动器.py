"""
定时启动器.py —— 不依赖 launchd/cron 的常驻进程
由某个父进程启动后，自己常驻，负责：
  - 每天北京时间 22:00（美股开盘前30分钟）拉起 长任务.py
  - 防止重复启动（检查锁文件）
  - 每天北京时间 05:00（美股收盘后）停掉长任务（可选）
  - 日志写到 运行日志/定时启动器.log
"""
import os
import sys
import time
import json
import datetime
import subprocess
import logging
import signal

os.makedirs("运行日志", exist_ok=True)
logger = logging.getLogger("定时启动器")
logger.setLevel(logging.INFO)
logger.propagate = False
_终端 = logging.StreamHandler(sys.stdout)
_终端.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(_终端)
_文件 = logging.FileHandler("运行日志/定时启动器.log", encoding="utf-8")
_文件.setFormatter(logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s"))
logger.addHandler(_文件)

项目目录 = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(项目目录, ".venv", "bin", "python")
长任务脚本 = os.path.join(项目目录, "长任务.py")
锁文件 = os.path.join(项目目录, "数据仓库", ".长任务.pid")
启动日志 = os.path.join(项目目录, "运行日志", "长任务.out.log")

# 美股开盘时间：北京时间 22:30（夏令时）/ 23:30（冬令时）
# 北京时间 22:00 启动，开盘前 30 分钟就位
启动小时 = 22  # 北京时间晚上 22:00

def 长任务在跑():
    """检查长任务进程是否在运行（通过锁文件里的 PID）。"""
    if not os.path.exists(锁文件):
        return False
    try:
        with open(锁文件, "r") as f:
            pid = int(f.read().strip())
        # 发信号 0 检测进程是否存活
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError):
        return False

def 拉起长任务():
    """启动长任务.py（后台，独立进程，不随父进程死亡）。"""
    logger.info("拉起长任务守护进程...")
    # 用 start_new_session 让它脱离父进程会话（父进程死了它也活）
    with open(启动日志, "a") as f:
        f.write(f"\n=== {datetime.datetime.now()} 定时启动器拉起长任务 ===\n")
        proc = subprocess.Popen(
            [PYTHON, 长任务脚本],
            stdout=f, stderr=subprocess.STDOUT,
            cwd=项目目录,
            start_new_session=True,  # 关键：脱离父进程会话
        )
    # 写 PID 锁文件
    with open(锁文件, "w") as f:
        f.write(str(proc.pid))
    logger.info(f"长任务已启动，PID={proc.pid}，日志: {启动日志}")
    return proc

def 停掉长任务():
    """停掉长任务进程。"""
    if not os.path.exists(锁文件):
        return
    try:
        with open(锁文件, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        logger.info(f"已发送 SIGTERM 给长任务 PID={pid}")
        os.remove(锁文件)
    except (ValueError, ProcessLookupError, FileNotFoundError):
        os.remove(锁文件) if os.path.exists(锁文件) else None

def 主循环():
    logger.info("=" * 50)
    logger.info("定时启动器 启动")
    logger.info(f"项目目录: {项目目录}")
    logger.info(f"每天北京时间 {启动小时}:00 自动拉起长任务")
    logger.info("=" * 50)

    已启动日期 = None
    while True:
        try:
            now = datetime.datetime.now()
            当前小时 = now.hour
            今天 = now.strftime("%Y-%m-%d")

            # 每天到了启动小时，且今天还没启动过
            if 当前小时 >= 启动小时 and 已启动日期 != 今天:
                if 长任务在跑():
                    logger.info(f"今天 {今天} 长任务已在跑，跳过")
                    已启动日期 = 今天
                else:
                    拉起长任务()
                    已启动日期 = 今天

            # 跨日重置（过了零点，允许今天再启动）
            if 当前小时 < 启动小时:
                已启动日期 = None

            # 健康检查：如果到了交易时段但长任务挂了，自动拉起
            if 启动小时 <= 当前小时 <= 23 and not 长任务在跑():
                logger.warning("长任务意外退出，自动重启...")
                拉起长任务()
                已启动日期 = 今天

            # 每 5 分钟检查一次
            time.sleep(300)
        except KeyboardInterrupt:
            logger.info("收到停止信号，定时启动器退出。")
            break
        except Exception as e:
            logger.error(f"主循环异常: {e}")
            time.sleep(60)

if __name__ == "__main__":
    主循环()
