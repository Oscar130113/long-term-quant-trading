"""
新闻源.py —— 长线项目 · 新闻数据获取模块

从东方财富（AkShare stock_news_em）拉取个股近期新闻，
供情绪分析模块（FinGPT）做真实的情感判断。

设计：
1. 拉取指定标的最近 N 条新闻（标题 + 内容摘要）。
2. 过滤掉过旧新闻（默认只看最近 7 天）。
3. 输出结构化列表，供情绪分析模块逐条或汇总分析。
"""

import akshare as ak
import pandas as pd
import datetime
import os


def 拉取新闻(股票代码="TSLA", 条数=10, 最近天数=7):
    """
    拉取个股近期新闻。
    返回 list[dict]，每项含 {标题, 内容, 时间, 来源, 链接}。
    失败返回空列表。
    """
    try:
        df = ak.stock_news_em(symbol=股票代码)
        if df is None or len(df) == 0:
            return []

        # 只保留最近 N 天内的新闻
        df['发布时间'] = pd.to_datetime(df['发布时间'])
        截止时间 = datetime.datetime.now() - datetime.timedelta(days=最近天数)
        df = df[df['发布时间'] >= 截止时间]

        # 取最近 条数 条
        df = df.head(条数)

        新闻列表 = []
        for _, row in df.iterrows():
            # 内容可能很长，截断到 500 字，避免 FinGPT 超长
            内容 = str(row.get('新闻内容', ''))
            if len(内容) > 500:
                内容 = 内容[:500]
            新闻列表.append({
                '标题': str(row.get('新闻标题', '')),
                '内容': 内容,
                '时间': str(row.get('发布时间', '')),
                '来源': str(row.get('文章来源', '')),
                '链接': str(row.get('新闻链接', '')),
            })
        return 新闻列表

    except Exception as e:
        print(f"  [失败] 新闻拉取失败: {e}")
        return []


def 汇总新闻文本(新闻列表, 最多条数=5):
    """
    把多条新闻汇总成一段文本，供 FinGPT 整体判断情绪。
    最多取前 N 条，避免 prompt 过长。
    """
    if not 新闻列表:
        return ""
    文本片段 = []
    for n in 新闻列表[:最多条数]:
        文本片段.append(f"- [{n['时间'][:10]}] {n['标题']}：{n['内容'][:150]}")
    return "\n".join(文本片段)


if __name__ == "__main__":
    # 独立测试：python 新闻源.py
    新闻 = 拉取新闻("TSLA", 条数=5, 最近天数=7)
    print(f"拉到 {len(新闻)} 条新闻：")
    for n in 新闻:
        print(f"  [{n['时间'][:10]}] {n['标题']}")
    print("\n--- 汇总文本 ---")
    print(汇总新闻文本(新闻))
