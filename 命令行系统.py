"""
命令行系统.py —— 长线价值增强项目 · 交互式命令行 UI

把项目从"跑一次就退出的脚本"升级成"可交互的系统"。
提供菜单式界面，让用户不用记命令行参数，直接按键操作。

功能菜单：
  1. 查看系统状态（配置 / 模式 / 持仓 / 盈亏）
  2. 跑一轮完整决策（更新数据 → 决策 → 交易）
  3. 切换交易模式（local / alpaca / live）
  4. 查看当前持仓与账本
  5. 查看最近新闻与情绪
  6. 查看运行日志（最近 N 行）
  7. 修改配置文件
  8. 选股测试（回测候选股，筛选目标股票池）
  0. 退出

用法：python 命令行系统.py
"""

import os
import sys
import json
import datetime
import subprocess

# ===== 颜色定义（兼容不支持颜色的终端） =====
class 颜色:
    红 = '\033[91m'
    绿 = '\033[92m'
    黄 = '\033[93m'
    蓝 = '\033[94m'
    紫 = '\033[95m'
    青 = '\033[96m'
    白 = '\033[97m'
    灰 = '\033[90m'
    粗 = '\033[1m'
    复位 = '\033[0m'


def 清屏():
    os.system('clear' if os.name == 'posix' else 'cls')


def 打印标题():
    清屏()
    print(f"{颜色.青}{颜色.粗}╔══════════════════════════════════════════════╗{颜色.复位}")
    print(f"{颜色.青}{颜色.粗}║        长线价值增强项目 · 交易系统           ║{颜色.复位}")
    print(f"{颜色.青}{颜色.粗}╚══════════════════════════════════════════════╝{颜色.复位}")
    print(f"{颜色.灰}  现在时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{颜色.复位}\n")


def 读取配置():
    """读取系统配置，返回字典。"""
    import configparser
    cfg = {}
    if not os.path.exists("系统配置.properties"):
        return cfg
    c = configparser.ConfigParser()
    c.read("系统配置.properties", encoding='utf-8')
    for k in c['DEFAULT']:
        cfg[k] = c['DEFAULT'][k]
    return cfg


def 打印主菜单():
    print(f"{颜色.粗}请选择操作（输入数字回车）：{颜色.复位}\n")
    print(f"  {颜色.绿}1{颜色.复位}  查看系统状态")
    print(f"  {颜色.绿}2{颜色.复位}  跑一轮完整决策（更新数据 → 决策 → 交易）")
    print(f"  {颜色.绿}3{颜色.复位}  切换交易模式")
    print(f"  {颜色.绿}4{颜色.复位}  查看当前持仓与账本")
    print(f"  {颜色.绿}5{颜色.复位}  查看最近新闻与情绪")
    print(f"  {颜色.绿}6{颜色.复位}  查看运行日志")
    print(f"  {颜色.绿}7{颜色.复位}  修改配置文件")
    print(f"  {颜色.绿}8{颜色.复位}  选股测试（回测候选股，筛选目标股票池）")
    print(f"  {颜色.红}0{颜色.复位}  退出系统")
    print()


# ===== 各功能实现 =====

def 功能_查看状态():
    打印标题()
    cfg = 读取配置()
    print(f"{颜色.粗}【系统状态】{颜色.复位}\n")
    print(f"  股票池      : {颜色.青}{cfg.get('tickers', 'TSLA')}{颜色.复位}")
    print(f"  交易模式    : {颜色.黄}{cfg.get('trade_mode', 'local')}{颜色.复位}")
    print(f"  预测周期    : {cfg.get('forecast_horizon', '5')} 天")
    print(f"  调度频率    : 每 {cfg.get('run_interval_days', '5')} 天")
    print(f"  初始资金    : ${cfg.get('initial_capital', '10000')}")
    print(f"  止损/止盈   : -{float(cfg.get('stop_loss_pct', '0.02'))*100:.0f}% / 回撤{float(cfg.get('trailing_take_profit_pct', '0.03'))*100:.0f}%")
    print(f"  数据更新    : {'开启' if cfg.get('enable_update', 'true') == 'true' else '关闭'}")
    print()

    # 检查 ollama 是否在线
    try:
        import requests
        s = requests.Session(); s.trust_env = False
        r = s.get("http://localhost:11434/api/version", timeout=3)
        ollama状态 = f"{颜色.绿}在线{颜色.复位} (v{r.json().get('version','?')})" if r.status_code == 200 else f"{颜色.红}离线{颜色.复位}"
    except Exception:
        ollama状态 = f"{颜色.红}离线{颜色.复位}"
    print(f"  Ollama 服务 : {ollama状态}")

    输入_返回()


def 功能_跑一轮():
    打印标题()
    print(f"{颜色.粗}开始执行一轮完整决策...{颜色.复位}\n")
    print(f"{颜色.灰}（这会联网拉行情+新闻，并调用本地 AI，耗时约 1-2 分钟）{颜色.复位}\n")

    # 调用 main.py 的核心逻辑（不重启进程，直接跑）
    import main as 主程序
    主程序.每日任务()

    print(f"\n{颜色.绿}✅ 一轮决策执行完毕。{颜色.复位}\n")
    输入_返回()


def 功能_切换模式():
    打印标题()
    模式说明 = {
        'local': '本地模拟（纯记账，不联网下单，最安全）',
        'alpaca': 'Alpaca 模拟盘（真实下单，虚拟资金）',
        'live': '实盘（⚠️ 未实现）',
    }
    print(f"{颜色.粗}【切换交易模式】{颜色.复位}\n")
    print(f"  1. local  - {模式说明['local']}")
    print(f"  2. alpaca - {模式说明['alpaca']}")
    print(f"  3. live   - {模式说明['live']}")
    print()
    选 = input(f"{颜色.黄}请选择新模式 (1/2/3): {颜色.复位}").strip()
    映射 = {'1': 'local', '2': 'alpaca', '3': 'live'}
    if 选 not in 映射:
        print(f"{颜色.红}无效选择。{颜色.复位}")
        输入_返回()
        return
    新模式 = 映射[选]

    # 修改配置文件
    文件 = "系统配置.properties"
    with open(文件, 'r', encoding='utf-8') as f:
        内容 = f.read()
    import re
    内容 = re.sub(r'trade_mode=.*', f'trade_mode={新模式}', 内容)
    with open(文件, 'w', encoding='utf-8') as f:
        f.write(内容)

    print(f"\n{颜色.绿}✅ 交易模式已切换为: {新模式}{颜色.复位}")
    print(f"{颜色.灰}（下次运行生效）{颜色.复位}\n")
    输入_返回()


def 功能_查看持仓():
    打印标题()
    print(f"{颜色.粗}【当前持仓与账本】{颜色.复位}\n")
    cfg = 读取配置()
    tickers = [t.strip().upper() for t in cfg.get('tickers', 'TSLA').split(',') if t.strip()]
    模式 = cfg.get('trade_mode', 'local')

    for t in tickers:
        账本文件 = os.path.join("数据仓库", t, f"{t}_账户账本.json")
        print(f"{颜色.青}── {t}（模式: {模式}）──{颜色.复位}")
        if 模式 == 'alpaca':
            # 从 Alpaca 读取真实账户
            try:
                from 交易模块 import 交易模块
                操盘手 = 交易模块(t, 模式='alpaca')
                p = 操盘手._读取账本()
                print(f"  现金: ${p['cash']:.2f}")
                print(f"  持仓: {p['shares']:.4f} 股 @ 成本 ${p['avg_price']:.2f}")
            except Exception as e:
                print(f"  {颜色.红}读取 Alpaca 失败: {e}{颜色.复位}")
        else:
            if os.path.exists(账本文件):
                with open(账本文件, 'r') as f:
                    p = json.load(f)
                print(f"  现金: ${p.get('cash', 0):.2f}")
                print(f"  持仓: {p.get('shares', 0):.4f} 股 @ 成本 ${p.get('avg_price', 0):.2f}")
                print(f"  最高价: ${p.get('highest_price', 0):.2f}")
                print(f"  更新日期: {p.get('date', '')}")
            else:
                print(f"  {颜色.灰}（暂无账本，尚未交易过）{颜色.复位}")
        print()
    输入_返回()


def 功能_查看新闻情绪():
    打印标题()
    print(f"{颜色.粗}【最近新闻与情绪】{颜色.复位}\n")
    try:
        import 新闻源
        cfg = 读取配置()
        tickers = [t.strip().upper() for t in cfg.get('tickers', 'TSLA').split(',') if t.strip()]
        for t in tickers:
            新闻 = 新闻源.拉取新闻(t, 条数=int(cfg.get('news_count', '8')), 最近天数=int(cfg.get('news_recent_days', '7')))
            print(f"{颜色.青}── {t} 最近新闻（{len(新闻)}条）──{颜色.复位}")
            for n in 新闻[:5]:
                print(f"  [{n['时间'][:10]}] {n['标题'][:50]}")
                print(f"     来源: {n['来源']}")
            print()
    except Exception as e:
        print(f"{颜色.红}拉取新闻失败: {e}{颜色.复位}")
    输入_返回()


def 功能_查看日志():
    打印标题()
    print(f"{颜色.粗}【运行日志】{颜色.复位}\n")
    日志目录 = "运行日志"
    if not os.path.exists(日志目录):
        print(f"{颜色.灰}暂无日志。{颜色.复位}")
        输入_返回()
        return
    日志文件 = sorted(os.listdir(日志目录))
    if not 日志文件:
        print(f"{颜色.灰}暂无日志。{颜色.复位}")
        输入_返回()
        return

    print(f"{颜色.灰}可用日志文件：{颜色.复位}")
    for i, f in enumerate(日志文件, 1):
        print(f"  {i}. {f}")
    print()
    选 = input(f"{颜色.黄}选择要查看的日志 (编号，回车返回): {颜色.复位}").strip()
    if not 选 or not 选.isdigit():
        return
    idx = int(选) - 1
    if idx < 0 or idx >= len(日志文件):
        print(f"{颜色.红}无效选择。{颜色.复位}")
        输入_返回()
        return
    文件 = os.path.join(日志目录, 日志文件[idx])
    with open(文件, 'r', encoding='utf-8') as f:
        行 = f.readlines()
    print(f"\n{颜色.青}── {日志文件[idx]}（最近 20 行）──{颜色.复位}")
    for line in 行[-20:]:
        print(f"  {line.rstrip()}")
    print()
    输入_返回()


def 功能_修改配置():
    打印标题()
    print(f"{颜色.粗}【修改配置文件】{颜色.复位}\n")
    print(f"{颜色.黄}将用系统默认编辑器打开配置文件，改完保存即可。{颜色.复位}")
    print(f"{颜色.灰}配置文件路径: 系统配置.properties{颜色.复位}\n")
    确认 = input(f"是否打开编辑？(y/n): ").strip().lower()
    if 确认 == 'y':
        subprocess.call(['open', '系统配置.properties'])
        print(f"{颜色.绿}已用默认编辑器打开。改完保存后，下次运行生效。{颜色.复位}")
    输入_返回()


def 功能_选股测试():
    打印标题()
    print(f"{颜色.粗}【选股测试 · 回测筛选】{颜色.复位}\n")
    cfg = 读取配置()
    预测周期 = int(cfg.get('forecast_horizon', '5'))
    测试天数 = int(cfg.get('backtest_days', '100'))

    print(f"{颜色.灰}方法：用历史行情+情绪训练 XGBoost，最后 {测试天数} 天当考卷，")
    print(f"统计「预测未来 {预测周期} 天涨跌」的胜率，超额 >5% 建议纳入股票池。{颜色.复位}\n")

    # 输入候选池
    默认池 = cfg.get('candidate_pool', 'TSLA,NVDA,AAPL,MSFT,META,GOOGL')
    print(f"{颜色.灰}当前候选池: {默认池}{颜色.复位}")
    输入池 = input(f"{颜色.黄}输入候选股票（逗号分隔，回车用默认）: {颜色.复位}").strip()
    候选池 = [s.strip().upper() for s in (输入池 or 默认池).split(',') if s.strip()]
    if not 候选池:
        print(f"{颜色.红}候选池为空。{颜色.复位}")
        输入_返回()
        return

    print(f"\n{颜色.灰}开始回测（每只约需几秒～几十秒，请稍候）...{颜色.复位}\n")

    # 调用选股测试模块
    try:
        import 选股测试 as 选股
        结果 = 选股.选股测试(候选池, 测试天数=测试天数, 预测周期=预测周期)
    except Exception as e:
        print(f"{颜色.红}选股测试失败: {e}{颜色.复位}")
        输入_返回()
        return

    if not 结果:
        输入_返回()
        return

    # 询问是否把 ✅ 标的写入目标股票池
    建议 = [r['股票'] for r in 结果 if r.get('超额', 0) > 5]
    if not 建议:
        print(f"{颜色.黄}本次没有「超额>5%」的标的，暂不更新股票池。{颜色.复位}")
        输入_返回()
        return

    print(f"{颜色.绿}建议纳入目标股票池: {', '.join(建议)}{颜色.复位}\n")
    确认 = input(f"{颜色.黄}是否将以上股票写入目标股票池（覆盖 tickers）？(y/n): {颜色.复位}").strip().lower()
    if 确认 == 'y':
        try:
            文件 = "系统配置.properties"
            with open(文件, 'r', encoding='utf-8') as f:
                内容 = f.read()
            import re
            # 保留已有 tickers 并去重合并（建议股放前面）
            现有 = [t.strip().upper() for t in cfg.get('tickers', '').split(',') if t.strip()]
            合并 = 建议 + [t for t in 现有 if t not in 建议]
            新池 = ','.join(合并)
            内容 = re.sub(r'tickers=.*', f'tickers={新池}', 内容)
            with open(文件, 'w', encoding='utf-8') as f:
                f.write(内容)
            print(f"{颜色.绿}✅ 已写入目标股票池: {新池}{颜色.复位}")
        except Exception as e:
            print(f"{颜色.红}写入配置失败: {e}{颜色.复位}")
    else:
        print(f"{颜色.灰}已跳过写入，股票池保持不变。{颜色.复位}")
    输入_返回()


def 输入_返回():
    print(f"{颜色.灰}─" * 50 + f"{颜色.复位}")
    input(f"{颜色.灰}按回车返回主菜单...{颜色.复位}")


# ===== 主循环 =====
def 主循环():
    while True:
        打印标题()
        打印主菜单()
        选 = input(f"{颜色.黄}> {颜色.复位}").strip()

        if 选 == '1':
            功能_查看状态()
        elif 选 == '2':
            功能_跑一轮()
        elif 选 == '3':
            功能_切换模式()
        elif 选 == '4':
            功能_查看持仓()
        elif 选 == '5':
            功能_查看新闻情绪()
        elif 选 == '6':
            功能_查看日志()
        elif 选 == '7':
            功能_修改配置()
        elif 选 == '8':
            功能_选股测试()
        elif 选 == '0':
            清屏()
            print(f"{颜色.绿}👋 感谢使用，再见！{颜色.复位}")
            sys.exit(0)
        else:
            print(f"{颜色.红}无效选择，请重新输入。{颜色.复位}")
            input(f"{颜色.灰}按回车继续...{颜色.复位}")


if __name__ == "__main__":
    try:
        主循环()
    except KeyboardInterrupt:
        清屏()
        print(f"{颜色.绿}已退出系统。{颜色.复位}")
        sys.exit(0)
