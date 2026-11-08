"""
交易模块.py —— 长线项目 · 交易模块（三模式可切换）

设计：策略层（止盈止损判断）与执行层（下单方式）解耦。
策略逻辑三模式完全一致，只有「下单」这一动作不同。

三种模式（通过构造函数 mode 参数切换）：
1. "local"   本地模拟：纯本地 JSON 记账，不联网，最安全，用于回测/调试。
2. "alpaca"  Alpaca 模拟盘：对接 paper-api，真实下单、虚拟资金，接近实战。
3. "live"    实盘：⚠️ 预留占位，暂不实现（防止误操作真实资金）。

账本结构（local 模式用本地文件，alpaca/live 模式用 Alpaca 账户数据）：
{
  "cash": 现金,
  "shares": 持股数(可含碎股),
  "avg_price": 持仓成本价,
  "highest_price": 建仓后最高价(用于移动止盈),
  "date": 最后更新日期
}
"""

import json
import os
import datetime
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

ALPACA_ENDPOINT = os.getenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2")
ALPACA_KEY_ID = os.getenv("ALPACA_API_KEY_ID", "")
ALPACA_SECRET = os.getenv("ALPACA_API_SECRET_KEY", "")


class 交易模块:
    def __init__(self, 股票代码, 初始资金=528.0, 费率=0.002,
                 止损比例=0.02, 移动止盈比例=0.03, 允许卖底仓=True,
                 模式="local", 仓位比例=0.10, 移动止盈激活线=0.02):
        """
        参数：
          模式 = "local" | "alpaca" | "live"
          仓位比例 = 单只股票最多占用「账户总资产」的比例（0.10 = 10%）
          移动止盈激活线 = 浮盈达到该比例后，才启用"从最高点回撤止盈"。
                           防止刚建仓微涨就误触发移动止盈（在亏损区误卖）。
        """
        self.股票代码 = 股票代码
        self.数据目录 = os.path.join("数据仓库", 股票代码)
        os.makedirs(self.数据目录, exist_ok=True)

        self.账本文件 = os.path.join(self.数据目录, f"{股票代码}_账户账本.json")
        self.初始资金 = 初始资金
        self.费率 = 费率
        self.止损比例 = 止损比例
        self.移动止盈比例 = 移动止盈比例
        self.允许卖底仓 = 允许卖底仓
        self.模式 = 模式
        self.仓位比例 = 仓位比例
        self.移动止盈激活线 = 移动止盈激活线

        if 模式 == "live":
            # ⚠️ 实盘暂不实现，直接拒绝，防止误操作真钱
            raise NotImplementedError("实盘(live)模式尚未实现，请使用 local 或 alpaca 模式。")

        os.makedirs("运行日志", exist_ok=True)
        self.logger = logging.getLogger(f"交易_{股票代码}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            fh = logging.FileHandler(f"运行日志/交易_{股票代码}.log", encoding='utf-8')
            fh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
            self.logger.addHandler(fh)

    # ============================================================
    # 底层：账本读写（local 模式用本地文件，alpaca 模式用 Alpaca 账户）
    # ============================================================

    def _读取账本(self):
        """读取当前持仓状态，三模式统一返回同样结构的字典。"""
        if self.模式 == "alpaca":
            return self._读取Alpaca账户()
        return self._读取本地账本()

    def _读取本地账本(self):
        if os.path.exists(self.账本文件):
            with open(self.账本文件, 'r') as f:
                p = json.load(f)
                p.setdefault("avg_price", p.get("last_price", 0.0))
                p.setdefault("highest_price", 0.0)
                return p
        return {"cash": self.初始资金, "shares": 0.0, "avg_price": 0.0,
                "highest_price": 0.0, "date": ""}

    def _保存本地账本(self, p):
        with open(self.账本文件, 'w') as f:
            json.dump(p, f, indent=4, ensure_ascii=False)

    # ============================================================
    # Alpaca 账户对接
    # ============================================================

    def _alpaca_headers(self):
        return {
            "APCA-API-KEY-ID": ALPACA_KEY_ID,
            "APCA-API-SECRET-KEY": ALPACA_SECRET,
        }

    def _读取Alpaca账户(self):
        """从 Alpaca 模拟盘读取账户现金和持仓，映射成本地账本结构。"""
        try:
            # 账户现金
            r = requests.get(f"{ALPACA_ENDPOINT}/account",
                             headers=self._alpaca_headers(), timeout=30)
            r.raise_for_status()
            acc = r.json()
            cash = float(acc.get("cash", 0))

            # 该股票持仓
            r2 = requests.get(f"{ALPACA_ENDPOINT}/positions/{self.股票代码}",
                              headers=self._alpaca_headers(), timeout=30)
            if r2.status_code == 200:
                pos = r2.json()
                shares = float(pos.get("qty", 0))
                avg_price = float(pos.get("avg_entry_price", 0))
            else:
                shares = 0.0
                avg_price = 0.0

            # 最高价无法从 Alpaca 直接拿，用本地账本缓存（跨运行保留）
            本地 = self._读取本地账本()
            highest = max(本地.get("highest_price", 0.0), avg_price)

            return {"cash": cash, "shares": shares, "avg_price": avg_price,
                    "highest_price": highest, "date": 本地.get("date", "")}

        except Exception as e:
            self.logger.error(f"读取 Alpaca 账户失败: {e}")
            raise

    def _alpaca下单(self, 方向, shares, 股票代码):
        """
        方向 = "buy" | "sell"
        shares 支持碎股（保留小数）
        """
        qty = str(round(shares, 6))  # Alpaca 碎股最多6位小数
        payload = {
            "symbol": 股票代码,
            "qty": qty,
            "side": 方向,
            "type": "market",
            "time_in_force": "day",
        }
        r = requests.post(f"{ALPACA_ENDPOINT}/orders",
                          headers=self._alpaca_headers(), json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

    def _取最新价格(self):
        """
        获取该股票的最新价格（用于盘中风控巡检）。
        alpaca 模式：优先用 Alpaca snapshot 实时报价；
        其他/失败：回退用 akshare 最新收盘价。
        返回 float，失败返回 None。
        """
        # 优先 Alpaca 实时快照
        try:
            r = requests.get(
                f"https://data.alpaca.markets/v2/stocks/{self.股票代码}/snapshot",
                headers=self._alpaca_headers(), timeout=10)
            if r.status_code == 200:
                snap = r.json()
                # 优先最新成交价，其次最新报价
                price = (snap.get("latestTrade") or {}).get("p") \
                        or (snap.get("latestQuote") or {}).get("ap") \
                        or (snap.get("minuteBar") or {}).get("c")
                if price:
                    return float(price)
        except Exception as e:
            self.logger.warning(f"Alpaca 快照失败({e})，回退 akshare")

        # 回退：akshare 最新收盘
        try:
            import akshare as ak
            df = ak.stock_us_daily(symbol=self.股票代码, adjust="qfq")
            if df is not None and len(df):
                return float(df['close'].iloc[-1])
        except Exception as e:
            self.logger.warning(f"akshare 取价失败: {e}")
        return None

    # ============================================================
    # 执行层：买入 / 卖出（按模式分发）
    # ============================================================

    def _单只最大额度(self, portfolio):
        """
        计算单只股票最多可动用的现金额度。
        alpaca 模式：账户总资产 × 仓位比例（动态浮动）。
        local 模式：初始资金 × 仓位比例。
        """
        if self.模式 == "alpaca":
            总资产 = portfolio["cash"] + portfolio["shares"] * portfolio["avg_price"]
            # 账户总资产（现金+已有该股持仓市值）≈ Alpaca portfolio_value
            return min(portfolio["cash"], 总资产 * self.仓位比例)
        return min(portfolio["cash"], self.初始资金 * self.仓位比例)

    def _买入(self, portfolio, close_price, today_date, 金额=None, 原因="建仓"):
        """
        买入（支持碎股、支持分批）。
        金额 = 本次买入动用的现金；None 时用「单只最大额度」满仓买入。
        返回本次实际买入的股数（失败返回 0）。
        """
        if 金额 is None:
            金额 = self._单只最大额度(portfolio)

        可用现金 = min(portfolio["cash"], 金额)
        if 可用现金 <= 0:
            self.logger.info("【警告】现金不足，无法买入")
            return 0
        shares = 可用现金 / close_price  # 碎股，保留小数
        cost = shares * close_price

        if self.模式 == "alpaca":
            try:
                self._alpaca下单("buy", shares, self.股票代码)
                self.logger.info(f"【买入·{原因}】Alpaca 下单 {shares:.4f} 股 @ ${close_price:.2f}（${cost:.2f}）")
            except Exception as e:
                self.logger.error(f"【买入·{原因}】Alpaca 下单失败: {e}")
                return 0
            # 更新本地账本（缓存 持仓/成本/最高价）
            portfolio["shares"] = portfolio.get("shares", 0.0) + shares
            # 加权平均成本
            旧成本 = portfolio.get("avg_price", 0.0)
            旧股数 = portfolio.get("shares", 0.0) - shares
            if 旧股数 > 0 and 旧成本 > 0:
                portfolio["avg_price"] = (旧成本 * 旧股数 + cost) / portfolio["shares"]
            else:
                portfolio["avg_price"] = close_price
            portfolio["highest_price"] = max(portfolio.get("highest_price", 0.0), close_price)
            portfolio["date"] = today_date
            self._保存本地账本(portfolio)
        else:
            portfolio["cash"] = portfolio["cash"] - cost
            portfolio["shares"] = portfolio.get("shares", 0.0) + shares
            旧成本 = portfolio.get("avg_price", 0.0)
            旧股数 = portfolio.get("shares", 0.0) - shares
            if 旧股数 > 0 and 旧成本 > 0:
                portfolio["avg_price"] = (旧成本 * 旧股数 + cost) / portfolio["shares"]
            else:
                portfolio["avg_price"] = close_price
            portfolio["highest_price"] = max(portfolio.get("highest_price", 0.0), close_price)
            portfolio["date"] = today_date
            self._保存本地账本(portfolio)
            self.logger.info(f"【买入·{原因}】{shares:.4f} 股 @ ${close_price:.2f}，花费 ${cost:.2f}")

        return shares

    def _卖出(self, portfolio, close_price, today_date, 原因, 股数=None):
        """
        卖出。股数 = 卖多少（支持分批，None=全卖）。
        返回实际卖出的股数。
        """
        total = portfolio["shares"]
        if total <= 0:
            return 0
        卖出股数 = total if 股数 is None else min(股数, total)
        if 卖出股数 <= 0:
            return 0

        proceeds = 卖出股数 * close_price * (1 - self.费率)
        pnl_pct = (close_price - portfolio["avg_price"]) / portfolio["avg_price"] * 100 if portfolio["avg_price"] > 0 else 0

        if self.模式 == "alpaca":
            try:
                self._alpaca下单("sell", 卖出股数, self.股票代码)
                self.logger.info(
                    f"【{原因}】Alpaca 卖出 {卖出股数:.4f} 股 @ ${close_price:.2f}，盈亏 {pnl_pct:.2f}%"
                )
            except Exception as e:
                self.logger.error(f"【{原因}】Alpaca 卖出下单失败: {e}")
                return 0
            portfolio["cash"] += proceeds
        else:
            portfolio["cash"] += proceeds
            self.logger.info(
                f"【{原因}】卖出 {卖出股数:.4f} 股 @ ${close_price:.2f}，"
                f"回收 ${proceeds:.2f}，盈亏 {pnl_pct:.2f}%"
            )

        # 更新持仓（剩余股数）
        portfolio["shares"] = total - 卖出股数
        if portfolio["shares"] <= 0:
            portfolio["shares"] = 0.0
            portfolio["avg_price"] = 0.0
            portfolio["highest_price"] = 0.0
        portfolio["date"] = today_date
        self._保存本地账本(portfolio)
        return 卖出股数

    # ============================================================
    # 策略层：信号处理 + 止盈止损（三模式完全一致）
    # ============================================================

    def 风控巡检(self):
        """
        盘中风控巡检（长任务模式用，可频繁调用）：
        拉最新价格，检查持仓是否触发 止损 / 移动止盈，触发立即卖出。

        返回:
          None        无持仓或取价失败（本次无事发生）
          "HOLD"      持仓但未触发风控
          "止损"/"移动止盈"  已触发并卖出
        """
        portfolio = self._读取账本()
        if portfolio["shares"] <= 0:
            return None

        price = self._取最新价格()
        if price is None:
            self.logger.warning("【巡检】取不到最新价格，跳过本轮")
            return None

        # 更新最高价（用于移动止盈）
        portfolio["highest_price"] = max(portfolio.get("highest_price", 0.0), price)
        avg = portfolio["avg_price"]
        pnl_pct = (price - avg) / avg if avg > 0 else 0

        # 止损：跌破成本 -止损比例 → 全卖（硬底线，不犹豫）
        if pnl_pct <= -self.止损比例:
            self._卖出(portfolio, price, datetime.datetime.now().strftime("%Y-%m-%d"), "止损")
            return "止损"

        # 移动止盈：先确认「曾真正盈利达到激活线」，再从最高点回撤 移动止盈比例
        # 修复 bug：之前只判断 highest_price > avg，导致微涨后回落就在亏损区误触发
        曾盈利 = portfolio["highest_price"] >= avg * (1 + self.移动止盈激活线)
        if 曾盈利 and portfolio["highest_price"] > avg and \
           (portfolio["highest_price"] - price) / portfolio["highest_price"] >= self.移动止盈比例:
            # 分批止盈：先卖一半，剩一半继续跑（若只剩微量则清仓）
            if portfolio["shares"] > 0.0001:
                卖一半 = portfolio["shares"] / 2
                self._卖出(portfolio, price, datetime.datetime.now().strftime("%Y-%m-%d"), "移动止盈·分批", 股数=卖一半)
            else:
                self._卖出(portfolio, price, datetime.datetime.now().strftime("%Y-%m-%d"), "移动止盈")
            return "移动止盈"

        # 未触发：仅更新本地缓存里的最高价
        if self.模式 == "alpaca":
            # alpaca 模式：只缓存 highest_price/date，不把 Alpaca 账户现金写进本地账本
            本地 = self._读取本地账本()
            本地["highest_price"] = portfolio["highest_price"]
            本地["date"] = datetime.datetime.now().strftime("%Y-%m-%d")
            self._保存本地账本(本地)
        else:
            self._保存本地账本(portfolio)
        self.logger.info(f"【巡检】持仓 {portfolio['shares']:.4f} 股 @ 最新 ${price:.2f} | "
                         f"浮盈 {pnl_pct*100:.2f}% (最高 ${portfolio['highest_price']:.2f})")
        return "HOLD"

    def 执行信号(self, signal):
        if not signal:
            self.logger.warning("未收到有效信号，放弃交易")
            return

        self.logger.info(f"--- 接收信号: {signal['action']} ({signal['date']}) | 模式={self.模式} ---")
        portfolio = self._读取账本()
        today_date = signal['date']
        close_price = signal['close_price']
        prob_up = signal.get('prob_up', 0.5)

        # ===== 1. 持仓风控检查（止盈止损，先于新信号） =====
        if portfolio["shares"] > 0:
            portfolio["highest_price"] = max(portfolio["highest_price"], close_price)
            avg = portfolio["avg_price"]
            pnl_pct = (close_price - avg) / avg if avg > 0 else 0

            if pnl_pct <= -self.止损比例:
                self._卖出(portfolio, close_price, today_date, "止损")
            elif portfolio["highest_price"] >= avg * (1 + self.移动止盈激活线) and \
                 portfolio["highest_price"] > avg and \
                 (portfolio["highest_price"] - close_price) / portfolio["highest_price"] >= self.移动止盈比例:
                # 分批止盈：先卖一半
                if portfolio["shares"] > 0.0001:
                    self._卖出(portfolio, close_price, today_date, "移动止盈·分批", 股数=portfolio["shares"] / 2)
                else:
                    self._卖出(portfolio, close_price, today_date, "移动止盈")
            else:
                self.logger.info(
                    f"【持有】持仓 {portfolio['shares']} 股 | 浮盈 {pnl_pct*100:.2f}% "
                    f"(成本 ${avg:.2f}, 最高 ${portfolio['highest_price']:.2f})"
                )

        # ===== 2. 模型看跌：主动减仓 =====
        if signal['action'] == "SELL" and portfolio["shares"] > 0:
            # 模型预测未来 N 天会跌 → 主动卖出（不等风控触发）
            self._卖出(portfolio, close_price, today_date, "模型看跌")
            self.logger.info("【信号】模型看跌，已清仓离场")

        # ===== 3. 买入信号：分批建仓（按概率分档） =====
        elif signal['action'] == "BUY":
            if portfolio["shares"] <= 0:
                # 空仓：按概率分档建仓
                额度 = self._单只最大额度(portfolio)
                if prob_up >= 0.65:
                    # 高置信：直接满仓额度
                    本次金额 = 额度
                    档位 = "满仓"
                elif prob_up >= 0.55:
                    # 中置信：半仓
                    本次金额 = 额度 * 0.5
                    档位 = "半仓"
                else:
                    # 低置信：轻仓试水（1/4）
                    本次金额 = 额度 * 0.25
                    档位 = "轻仓"
                self.logger.info(f"【信号】BUY 概率{prob_up*100:.1f}% → {档位}建仓（${本次金额:.2f}）")
                self._买入(portfolio, close_price, today_date, 金额=本次金额, 原因=档位)
            else:
                # 已持仓：概率够高才补仓（补到满仓额度为止）
                额度 = self._单只最大额度(portfolio)
                当前市值 = portfolio["shares"] * close_price
                if prob_up >= 0.65 and 当前市值 < 额度 * 0.95:
                    补仓金额 = min(额度 - 当前市值, portfolio["cash"])
                    if 补仓金额 > 0:
                        self.logger.info(f"【信号】高置信BUY，补仓 ${补仓金额:.2f}")
                        self._买入(portfolio, close_price, today_date, 金额=补仓金额, 原因="补仓")
                else:
                    self.logger.info("【持有】已有持仓，暂不加仓")

        # ===== 4. 汇总 =====
        if self.模式 == "alpaca":
            # alpaca 模式：重新读真实账户，显示准确的总资产
            try:
                acc = self._读取Alpaca账户()
                总资产 = acc["cash"] + acc["shares"] * close_price
                self.logger.info(
                    f"【账户】总资产: ${总资产:.2f} | 账户现金: ${acc['cash']:.2f} | "
                    f"{self.股票代码} 持仓: {acc['shares']:.4f} 股"
                )
            except Exception as e:
                self.logger.warning(f"【账户】读取 Alpaca 账户失败: {e}")
        else:
            总资产 = portfolio["cash"] + portfolio["shares"] * close_price
            self.logger.info(
                f"【账户】总资产: ${总资产:.2f} | 现金: ${portfolio['cash']:.2f} | 持仓: {portfolio['shares']} 股"
            )
        self.logger.info("-----------------------------------")
