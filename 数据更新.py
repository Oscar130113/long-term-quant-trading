"""
数据更新.py —— 长线项目 · 数据更新模块

改造自短线项目：核心变化是把 SentiSense 情绪 API 替换为
FinGPT(本地 Ollama) 为主 + DeepSeek 兜底的情绪分析。
行情数据仍用 AkShare（新浪美股接口）。

长线优化：预测周期可配置（forecast_horizon），label 从"次日涨跌"改为"未来N天涨跌"。
"""

import pandas as pd
import akshare as ak
import datetime
import os

import 情绪分析

数据目录 = "数据仓库"


def _计算情绪代理(df):
    """
    用行情数据计算"市场情绪代理"（免费、可回溯到任意历史日期）。

    真实新闻情绪（FinGPT）只能覆盖最近几天，历史日期回填不了。
    为了让"情绪"这一维在回测/训练中真正有值（而不是全 0），
    这里用几个经典的可计算市场情绪信号，合成一个 [-1, +1] 区间的代理情绪：

      · 动量：近 5 日累计涨跌幅（涨→情绪偏多）
      · 跳空缺口：开盘相对昨收的跳空幅度（跳空高开→偏多）
      · 成交量异动：相对 20 日均量的放大程度（放量→情绪活跃）

    三者标准化后取平均并截断到 [-1, +1]，作为"情绪代理"。
    真实 FinGPT 情绪数据若有对应日期，会在选股测试里优先覆盖它。
    """
    # 动量：近 5 日收益（%）
    动量 = df['close'].pct_change(5)
    # 跳空缺口：开盘 vs 昨收
    跳空 = (df['open'] - df['close'].shift(1)) / df['close'].shift(1)
    # 成交量异动：相对 20 日均量
    量异动 = (df['volume'] - df['volume'].rolling(20).mean()) / df['volume'].rolling(20).mean()

    # 各自缩放到 [-1,1]（用固定阈值裁剪，避免极端值主导）
    def 缩放(s, 阈值):
        return (s / 阈值).clip(-1, 1)

    情绪 = (
        缩放(动量, 0.05) +          # 5日涨 5% 记满 +1
        缩放(跳空, 0.02) +          # 跳空 2% 记满 +1
        缩放(量异动, 1.0)           # 量能翻倍记满 +1
    ) / 3.0

    return 情绪.round(4)


def 更新每日数据(股票代码="TSLA", 均线w=20, RSIw=14, MACDf=12, MACDs=26, MACD信号=9,
                 预测周期=5, 起始年份=2020, 新闻条数=8, 新闻最近天数=7, 新闻分析上限=5):
    print(f"[{datetime.datetime.now()}] 开始更新 {股票代码} 数据...")
    股票文件夹 = os.path.join(数据目录, 股票代码)
    os.makedirs(股票文件夹, exist_ok=True)

    # ===== 1. 行情数据 =====
    try:
        df = ak.stock_us_daily(symbol=股票代码, adjust="qfq").reset_index().rename(columns={'date': 'Date'})
        df['Date'] = pd.to_datetime(df['Date'])
        df = df[df['Date'] >= f'{起始年份}-01-01'].copy()

        df['ma20'] = df['close'].rolling(window=均线w).mean()
        df['ma_deviation'] = (df['close'] - df['ma20']) / df['ma20']

        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=RSIw).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=RSIw).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss))

        exp12 = df['close'].ewm(span=MACDf, adjust=False).mean()
        exp26 = df['close'].ewm(span=MACDs, adjust=False).mean()
        df['macd_hist'] = (exp12 - exp26) - (exp12 - exp26).ewm(span=MACD信号, adjust=False).mean()

        df['vol_change'] = (df['volume'] - df['volume'].rolling(window=20).mean()) / df['volume'].rolling(window=20).mean()

        # 💡 长线关键改造：label 从"次日涨跌"改为"未来 N 天涨跌"
        df['next_return'] = (df['close'].shift(-预测周期) - df['close']) / df['close']
        df['label'] = (df['next_return'] > 0).astype(int)

        df.dropna(subset=['ma_deviation', 'rsi', 'macd_hist', 'vol_change'], inplace=True)

        # ===== 合并历史情绪列（避免情绪列长期为空/全0） =====
        # 真实 FinGPT 情绪只覆盖"最近几天"（历史回填不了），
        # 历史日期用"市场情绪代理"填充，让情绪这一维在训练中真正有值。
        df['sentiment'] = _计算情绪代理(df)
        df.to_csv(os.path.join(股票文件夹, f"{股票代码}_行情数据.csv"), index=False)
        print(f"  [成功] 行情数据已更新: {股票代码} (预测周期={预测周期}天, 含情绪代理列)")
    except Exception as e:
        print(f"  [失败] 行情更新失败: {e}")

    # ===== 2. 情绪数据（FinGPT 为主 + DeepSeek 兜底，实时累积） =====
    情绪分析.分析并保存(股票代码, 公司名=股票代码,
                       新闻条数=新闻条数, 最近天数=新闻最近天数, 分析上限=新闻分析上限)


if __name__ == "__main__":
    更新每日数据("TSLA", 预测周期=5)
