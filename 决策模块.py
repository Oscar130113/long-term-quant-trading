"""
决策模块.py —— 长线项目 · 决策模块

改造自短线项目，核心变化：
1. 预测周期从 1 天改为可配置的 N 天（默认 5 天），长线低频。
2. 情绪特征来源从 SentiSense 改为 DeepSeek 情绪序列。
3. 保留 QQQ 大盘 200 日均线过滤（牛市做多、熊市空仓的保命底牌）。
"""

import pandas as pd
import akshare as ak
import os
from xgboost import XGBClassifier
import warnings
import logging

warnings.filterwarnings('ignore')


class 决策模块:
    def __init__(self, 股票代码, 大盘均线=200, 树数量=100, 最大深度=3, 学习率=0.05, 预测周期=5):
        self.股票代码 = 股票代码
        self.数据目录 = os.path.join("数据仓库", 股票代码)
        self.大盘均线 = 大盘均线
        self.树数量 = 树数量
        self.最大深度 = 最大深度
        self.学习率 = 学习率
        self.预测周期 = 预测周期

        os.makedirs("运行日志", exist_ok=True)
        self.logger = logging.getLogger(f"决策_{股票代码}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            fh = logging.FileHandler(f"运行日志/决策_{股票代码}.log", encoding='utf-8')
            fh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
            self.logger.addHandler(fh)

    def 生成信号(self):
        self.logger.info(f"--- 开始生成长线决策 (预测周期={self.预测周期}天) ---")
        try:
            数据文件 = os.path.join(self.数据目录, f"{self.股票代码}_行情数据.csv")
            if not os.path.exists(数据文件):
                raise Exception("找不到行情数据")
            df = pd.read_csv(数据文件).assign(Date=lambda d: pd.to_datetime(d['Date']))

            # ===== 情绪特征：行情CSV自带代理情绪，用真实情绪覆盖（若有） =====
            # 行情数据.csv 已含 sentiment（市场情绪代理）。
            # 情绪数据.csv 存真实 FinGPT 情绪（逐日累积），有对应日期则覆盖代理值。
            情绪文件 = os.path.join(self.数据目录, f"{self.股票代码}_情绪数据.csv")
            if 'sentiment' not in df.columns:
                df['sentiment'] = 0.0
            if os.path.exists(情绪文件):
                sent_df = pd.read_csv(情绪文件)
                if 'date' in sent_df.columns:
                    sent_df = sent_df.rename(columns={'date': 'Date'})
                if 'Date' in sent_df.columns and 'sentiment' in sent_df.columns:
                    sent_df['Date'] = pd.to_datetime(sent_df['Date'])
                    sent_df = sent_df[['Date', 'sentiment']].rename(columns={'sentiment': '真实情绪'})
                    df = pd.merge(df, sent_df, on='Date', how='left')
                    有效 = df['真实情绪'].notna()
                    df.loc[有效, 'sentiment'] = df.loc[有效, '真实情绪']
                    df.drop(columns=['真实情绪'], inplace=True)
            df['sentiment'] = df['sentiment'].fillna(0)

            # 大盘过滤
            大盘目录 = os.path.join("数据仓库", "QQQ")
            os.makedirs(大盘目录, exist_ok=True)
            raw_qqq = ak.stock_us_daily(symbol="QQQ", adjust="qfq").reset_index().rename(columns={'date': 'Date', 'close': 'qqq_close'})
            raw_qqq['Date'] = pd.to_datetime(raw_qqq['Date'])
            raw_qqq['market_trend'] = (raw_qqq['qqq_close'] > raw_qqq['qqq_close'].rolling(window=self.大盘均线).mean()).astype(int)
            qqq = raw_qqq[['Date', 'market_trend']]

            df = pd.merge(df, qqq, on='Date', how='left')
            df['market_trend'] = df['market_trend'].fillna(1)
            df.dropna(subset=['rsi', 'ma_deviation', 'macd_hist', 'vol_change'], inplace=True)

            # 💡 长线关键：label 预测未来 N 天涨跌
            df['label'] = (df['close'].shift(-self.预测周期) > df['close']).astype(int)
            df.dropna(inplace=True)

            X = df[['rsi', 'ma_deviation', 'macd_hist', 'vol_change', 'sentiment']]
            y = df['label']
            ratio = (len(y) - sum(y)) / sum(y) if sum(y) > 0 else 1.0

            model = XGBClassifier(
                n_estimators=self.树数量,
                max_depth=self.最大深度,
                learning_rate=self.学习率,
                random_state=42,
                scale_pos_weight=ratio
            )
            model.fit(X, y)

            latest = df.iloc[-1]
            X_today = df[['rsi', 'ma_deviation', 'macd_hist', 'vol_change', 'sentiment']].tail(1)
            prob_up = model.predict_proba(X_today)[0][1]
            predict_up = int(model.predict(X_today)[0])
            market_ok = bool(latest['market_trend'] == 1)

            # 动作：BUY（看涨+牛市）/ SELL（看跌）/ HOLD（其他）
            if predict_up == 1 and market_ok:
                action = "BUY"
            elif predict_up == 0:
                action = "SELL"
            else:
                action = "HOLD"

            signal = {
                "date": latest['Date'].strftime('%Y-%m-%d'),
                "ticker": self.股票代码,
                "close_price": round(float(latest['close']), 2),
                "market_ok": market_ok,
                "prob_up": round(float(prob_up), 4),
                "forecast_horizon": self.预测周期,
                "action": action
            }

            self.logger.info(
                f"决策完成: {action} | 未来{self.预测周期}天上涨概率: {prob_up*100:.1f}% | 收盘价: {signal['close_price']}"
            )
            return signal
        except Exception as e:
            self.logger.error(f"决策模块报错: {e}")
            return None
