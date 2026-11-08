"""
选股测试.py —— 长线价值增强项目 · 选股回测工具

用途：
  对候选股票池做历史回测，判断哪些股票值得放进"目标股票池"。
  核心逻辑：用历史数据训练 XGBoost 模型，抽取最近 N 天做测试，
  统计"预测未来 H 天涨跌"的胜率，胜率高的股票才建议纳入。

方法（防数据泄漏）：
  1. 取该股票的全部历史行情，计算技术指标 + 情绪特征。
  2. 严格切分：最后 N 天(默认100)作为"考卷"，之前的作为"教材"。
  3. 只用"教材"训练模型，用"考卷"预测，绝不混用（避免未来函数泄漏）。
  4. 统计考卷期的胜率、收益率，并与"傻瓜买入持有"基准对比。

用法：
  python 选股测试.py                          # 测默认候选池
  python 选股测试.py TSLA,NVDA,AAPL,MSFT     # 测指定候选池
"""

import os
import sys
import json
import pandas as pd
import akshare as ak
import warnings
from xgboost import XGBClassifier

warnings.filterwarnings('ignore')

数据目录 = "数据仓库"


def _计算情绪代理(df):
    """
    用行情数据计算"市场情绪代理"（免费、可回溯到任意历史日期）。
    与 数据更新.py 里的同名函数逻辑一致，这里独立一份以避免跨模块耦合。

    合成三个可计算的市场情绪信号，映射到 [-1, +1]：
      动量(近5日收益) + 跳空缺口(开-昨收) + 成交量异动(相对20日均量)
    """
    动量 = df['close'].pct_change(5)
    跳空 = (df['open'] - df['close'].shift(1)) / df['close'].shift(1)
    量异动 = (df['volume'] - df['volume'].rolling(20).mean()) / df['volume'].rolling(20).mean()

    def 缩放(s, 阈值):
        return (s / 阈值).clip(-1, 1)

    情绪 = (
        缩放(动量, 0.05) +
        缩放(跳空, 0.02) +
        缩放(量异动, 1.0)
    ) / 3.0
    return 情绪.round(4)


def 拉取行情(股票代码, 起始年份=2020):
    """拉取个股历史行情并计算技术指标，返回 DataFrame（含 sentiment 列）。"""
    文件 = os.path.join(数据目录, 股票代码, f"{股票代码}_行情数据.csv")
    # 优先用本地缓存（数据更新已算好指标和 label，且已含情绪代理列）
    if os.path.exists(文件):
        df = pd.read_csv(文件).assign(Date=lambda d: pd.to_datetime(d['Date']))

        # 若缓存里没有 sentiment 列（旧数据），用情绪代理补上
        if 'sentiment' not in df.columns:
            df['sentiment'] = _计算情绪代理(df)

        # 用真实 FinGPT 情绪覆盖代理值（若有对应日期的真实情绪）
        情绪文件 = os.path.join(数据目录, 股票代码, f"{股票代码}_情绪数据.csv")
        真实覆盖数 = 0
        if os.path.exists(情绪文件):
            sent = pd.read_csv(情绪文件)
            if 'date' in sent.columns and 'Date' not in sent.columns:
                sent = sent.rename(columns={'date': 'Date'})
            if 'Date' in sent.columns and 'sentiment' in sent.columns:
                sent['Date'] = pd.to_datetime(sent['Date'])
                # 先保存代理值，再覆盖真实值
                df = pd.merge(df, sent[['Date', 'sentiment']].rename(columns={'sentiment': '真实情绪'}), on='Date', how='left')
                有效 = df['真实情绪'].notna()
                真实覆盖数 = int(有效.sum())
                df.loc[有效, 'sentiment'] = df.loc[有效, '真实情绪']
                df.drop(columns=['真实情绪'], inplace=True)

        df['sentiment'] = df['sentiment'].fillna(0).round(4)
        # 记录情绪覆盖情况（供结果展示）
        df.attrs['真实情绪覆盖数'] = 真实覆盖数
        return df

    # 否则联网拉取并计算
    df = ak.stock_us_daily(symbol=股票代码, adjust="qfq").reset_index().rename(columns={'date': 'Date'})
    df['Date'] = pd.to_datetime(df['Date'])
    df = df[df['Date'] >= f'{起始年份}-01-01'].copy()

    df['ma20'] = df['close'].rolling(window=20).mean()
    df['ma_deviation'] = (df['close'] - df['ma20']) / df['ma20']
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    df['rsi'] = 100 - (100 / (1 + gain / loss))
    exp12 = df['close'].ewm(span=12, adjust=False).mean()
    exp26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd_hist'] = (exp12 - exp26) - (exp12 - exp26).ewm(span=9, adjust=False).mean()
    df['vol_change'] = (df['volume'] - df['volume'].rolling(window=20).mean()) / df['volume'].rolling(window=20).mean()

    # 情绪：用代理填充历史，真实情绪覆盖（若有）
    df['sentiment'] = _计算情绪代理(df)
    情绪文件 = os.path.join(数据目录, 股票代码, f"{股票代码}_情绪数据.csv")
    if os.path.exists(情绪文件):
        sent = pd.read_csv(情绪文件)
        if 'date' in sent.columns and 'Date' not in sent.columns:
            sent = sent.rename(columns={'date': 'Date'})
        if 'Date' in sent.columns and 'sentiment' in sent.columns:
            sent['Date'] = pd.to_datetime(sent['Date'])
            df = pd.merge(df, sent[['Date', 'sentiment']].rename(columns={'sentiment': '真实情绪'}), on='Date', how='left')
            有效 = df['真实情绪'].notna()
            df.loc[有效, 'sentiment'] = df.loc[有效, '真实情绪']
            df.drop(columns=['真实情绪'], inplace=True)

    df['sentiment'] = df['sentiment'].fillna(0).round(4)
    df.dropna(subset=['ma_deviation', 'rsi', 'macd_hist', 'vol_change'], inplace=True)
    return df


def 回测单只股票(股票代码, 测试天数=100, 预测周期=5,
                 树数量=100, 最大深度=3, 学习率=0.05, 随机种子=42):
    """
    对单只股票做回测，返回结果字典。
    返回 None 表示数据不足无法回测。
    """
    try:
        df = 拉取行情(股票代码)
    except Exception as e:
        return {"股票": 股票代码, "错误": f"拉取失败: {e}"}

    if df is None or len(df) < 测试天数 + 200:
        return {"股票": 股票代码, "错误": f"数据不足(仅{0 if df is None else len(df)}天)"}

    真实情绪覆盖数 = df.attrs.get('真实情绪覆盖数', 0)

    # 计算 label：未来 H 天涨跌
    df['label'] = (df['close'].shift(-预测周期) > df['close']).astype(int)
    df.dropna(subset=['label'], inplace=True)

    特征 = ['rsi', 'ma_deviation', 'macd_hist', 'vol_change', 'sentiment']

    # 严格切分：最后 N 天为考卷，之前为教材
    测试集 = df.tail(测试天数).copy()
    训练集 = df.head(len(df) - 测试天数).copy()

    if len(训练集) < 100:
        return {"股票": 股票代码, "错误": "训练数据不足"}

    X_train = 训练集[特征]
    y_train = 训练集['label']
    X_test = 测试集[特征]
    y_test = 测试集['label']

    # 平衡正负样本权重
    ratio = (len(y_train) - sum(y_train)) / sum(y_train) if sum(y_train) > 0 else 1.0

    model = XGBClassifier(
        n_estimators=树数量, max_depth=最大深度, learning_rate=学习率,
        random_state=随机种子, scale_pos_weight=ratio, eval_metric='logloss'
    )
    model.fit(X_train, y_train)

    # 预测考卷期
    y_pred = model.predict(X_test)

    # 统计胜率：预测"涨"且实际"涨" 的比例（在预测为涨的情况下）
    预测涨 = (y_pred == 1).sum()
    预测涨且对 = ((y_pred == 1) & (y_test == 1)).sum()
    预测涨胜率 = 预测涨且对 / 预测涨 * 100 if 预测涨 > 0 else 0

    # 整体准确率
    总准确率 = (y_pred == y_test).sum() / len(y_test) * 100

    # 基准：傻瓜买入持有的胜率（实际涨的天数占比）
    基准胜率 = y_test.sum() / len(y_test) * 100

    # 模拟收益：预测涨就买，持有一个预测周期
    # 用测试集里的"未来收益"近似（此处用 label 对应的真实涨跌做方向性统计）
    信号数 = 预测涨
    命中 = 预测涨且对

    return {
        "股票": 股票代码,
        "训练天数": len(训练集),
        "测试天数": len(测试集),
        "信号数": int(信号数),
        "命中数": int(命中),
        "预测涨胜率": round(预测涨胜率, 2),
        "整体准确率": round(总准确率, 2),
        "基准胜率": round(基准胜率, 2),
        "超额": round(预测涨胜率 - 基准胜率, 2),
        "情绪覆盖天数": 真实情绪覆盖数,
    }


def 选股测试(候选池, 测试天数=100, 预测周期=5):
    """对候选池逐只回测，输出排名表。"""
    print(f"\n{'='*60}")
    print(f"选股回测：预测未来 {预测周期} 天，用最近 {测试天数} 天做测试")
    print(f"{'='*60}\n")

    结果列表 = []
    for 股票 in 候选池:
        print(f"正在回测 {股票} ...")
        r = 回测单只股票(股票, 测试天数=测试天数, 预测周期=预测周期)
        if r and '错误' not in r:
            结果列表.append(r)
        else:
            print(f"  ⚠️ {r.get('错误', '未知错误')}")
            print()

    if not 结果列表:
        print("没有可回测的股票。")
        return

    # 按"预测涨胜率"排序
    结果列表.sort(key=lambda x: x['预测涨胜率'], reverse=True)

    print(f"\n{'='*72}")
    print(f"{'排名结果（按预测涨胜率排序）':^72}")
    print(f"{'='*72}")
    print(f"{'排名':<4}{'股票':<8}{'预测涨胜率':<12}{'整体准确率':<12}{'基准胜率':<10}{'超额':<10}{'信号':<6}{'真情绪':<8}")
    print('-'*72)
    for i, r in enumerate(结果列表, 1):
        标记 = " ✅" if r['超额'] > 5 else ""
        超额文本 = f"{r['超额']:+.2f}%"
        情绪覆盖 = r.get('情绪覆盖天数', 0)
        情绪文本 = f"{情绪覆盖}天" if 情绪覆盖 > 0 else "代理"
        print(f"{i:<4}{r['股票']:<8}{r['预测涨胜率']:.2f}%{'':<7}{r['整体准确率']:.2f}%{'':<7}{r['基准胜率']:.2f}%{'':<5}{超额文本:<10}{r['信号数']:<6}{情绪文本:<8}{标记}")

    print(f"\n{'='*72}")
    print("说明：")
    print("  · 预测涨胜率：模型说'会涨'时，实际真涨的比例（越高越好）")
    print("  · 基准胜率：不预测、天天持有的上涨天数占比")
    print("  · 超额 = 预测涨胜率 - 基准胜率（>5% 表示模型有真实选股能力）")
    print("  · 真情绪列：有多少天用了真实 FinGPT 新闻情绪（其余用市场情绪代理填充）")
    print("  · ✅ 标记 = 建议纳入目标股票池")
    print(f"{'='*72}\n")

    return 结果列表


if __name__ == "__main__":
    # 候选池：命令行参数指定，或默认美股热门科技股
    if len(sys.argv) > 1:
        候选池 = [s.strip().upper() for s in sys.argv[1].split(',') if s.strip()]
    else:
        候选池 = ["TSLA", "NVDA", "AAPL", "MSFT", "META", "GOOGL"]

    print(f"候选股票池: {', '.join(候选池)}")
    选股测试(候选池, 测试天数=100, 预测周期=5)
