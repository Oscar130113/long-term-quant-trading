"""
历史情绪回填.py —— 长线价值增强项目 · 真实历史情绪序列回填

解决的问题：
  情绪引擎（本地 FinGPT）只能分析"当天"新闻，回填不了历史。
  导致选股回测时"情绪"这一维长期是空的（之前只能填 0 或用代理情绪）。

本脚本从两个免费源拉取【真实历史新闻情绪】，按天聚合成逐日情绪序列，
写入 数据仓库/<代码>/<代码>_情绪数据.csv（与现有格式兼容，日期列名为 date）。

两个源（能拉多少算多少，互为补充）：
  1. Alpha Vantage NEWS_SENTIMENT —— 自带情绪分（无需本地 FinGPT，快）
     · 免费档 25 次/天，历史约到 2022 年。
     · 需要 key：ALPHA_VANTAGE_API_KEY
  2. Finnhub company-news —— 拉历史新闻标题，本地 FinGPT 标注（英文，无需翻译）
     · 免费档 1 年历史，30 次/秒。
     · 需要 key：FINNHUB_API_KEY

用法：
  ./.venv/bin/python 历史情绪回填.py TSLA            # 回填单只
  ./.venv/bin/python 历史情绪回填.py TSLA,NVDA,AAPL   # 回填多只
  ./.venv/bin/python 历史情绪回填.py                   # 回填配置里的 tickers

注意：
  · Finnhub 标注依赖本地 ollama（FinGPT），逐条较慢（冷加载约十几秒/条）。
  · 可先不填 Finnhub key，只用 Alpha Vantage 自带情绪分跑通。
"""

import os
import sys
import json
import time
import datetime
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

ALPHA_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "").strip()

数据目录 = "数据仓库"

# 直连（绕过系统代理，避免 502）
直连 = requests.Session()
直连.trust_env = False


# ============================================================
# 源 1：Alpha Vantage NEWS_SENTIMENT（自带情绪分）
# ============================================================

def _拉Alpha情绪(股票代码, 起始日期, 结束日期):
    """
    拉取 Alpha Vantage 的情绪分，返回 [(date_str, sentiment)] 列表。
    免费档限制：time_from 最早到约 2022 年，limit 最多 1000。
    """
    if not ALPHA_KEY:
        return [], "未配置 ALPHA_VANTAGE_API_KEY"

    # ⚠️ 2026 年起 Alpha Vantage 的 NEWS_SENTIMENT 已改为 premium 端点，
    # 免费 key 会收到 "premium endpoint" 提示。这里直接检测并跳过。
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": 股票代码,
        "time_from": 起始日期,   # YYYYMMDDTHHMM
        "time_to": 结束日期,
        "limit": 1000,
        "sort": "LATEST",
        "apikey": ALPHA_KEY,
    }
    try:
        r = 直连.get(url, params=params, timeout=30)
        data = r.json()
    except Exception as e:
        return [], f"Alpha 请求失败: {e}"

    if "feed" not in data:
        # 可能是额度耗尽或 premium 限制
        err = data.get("Note", "") or data.get("Information", "") or json.dumps(data)[:120]
        return [], f"Alpha 返回异常(可能已改 premium): {err}"

    逐日 = {}  # date_str -> [scores]
    for item in data["feed"]:
        # 时间：20220901T123456 -> 2022-09-01
        ts = item.get("time_published", "")
        if len(ts) < 8:
            continue
        日期 = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        # 找到该股票的 sentiment
        for tk in item.get("ticker_sentiment", []):
            if tk.get("ticker", "").upper() == 股票代码.upper():
                score = tk.get("ticker_sentiment_score")
                rel = tk.get("relevance_score", "1")
                try:
                    score = float(score)
                    rel = float(rel)
                except (TypeError, ValueError):
                    continue
                # 只保留相关性高的（relevance > 0.3），并用相关性加权
                if rel >= 0.3:
                    逐日.setdefault(日期, []).append((score, rel))

    结果 = []
    for 日期 in sorted(逐日.keys()):
        分数们 = 逐日[日期]
        # 相关性加权平均
        加权 = sum(s * r for s, r in 分数们) / sum(r for _, r in 分数们)
        结果.append((日期, round(加权, 4)))
    return 结果, None


# ============================================================
# 源 2：Finnhub company-news（标题 + 本地 FinGPT 标注）
# ============================================================

def _拉Finnhub单时段(股票代码, 起始日期, 结束日期):
    """拉 Finnhub 单一时段的新闻（不分页），返回 [(date_str, headline, id)]。"""
    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": 股票代码,
        "from": 起始日期,   # YYYY-MM-DD
        "to": 结束日期,
        "token": FINNHUB_KEY,
    }
    r = 直连.get(url, params=params, timeout=30)
    if r.status_code != 200:
        raise Exception(f"Finnhub HTTP {r.status_code}: {r.text[:100]}")
    data = r.json()
    if not isinstance(data, list):
        raise Exception(f"Finnhub 返回异常: {str(data)[:100]}")

    结果 = []
    for item in data:
        dt = item.get("datetime", 0)
        headline = item.get("headline", "").strip()
        if not headline:
            continue
        日期 = datetime.datetime.fromtimestamp(dt).strftime("%Y-%m-%d")
        结果.append((日期, headline, item.get("id", 0)))
    return 结果


def _拉Finnhub新闻(股票代码, 起始日期, 结束日期):
    """
    拉 Finnhub 历史新闻，返回 [(date_str, headline)] 列表。
    ⚠️ 免费档必须分时段请求才能拿到历史（一次请求只返回最近~1周），
    所以按「周」切片往回扫，逐段拉取并去重。
    """
    if not FINNHUB_KEY:
        return [], "未配置 FINNHUB_API_KEY"

    起始 = datetime.datetime.strptime(起始日期, "%Y-%m-%d")
    结束 = datetime.datetime.strptime(结束日期, "%Y-%m-%d")

    已见id = set()
    结果 = []
    当前 = 结束
    段数 = 0
    while 当前 >= 起始:
        段起始 = 当前 - datetime.timedelta(days=7)
        if 段起始 < 起始:
            段起始 = 起始
        段数 += 1
        try:
            一批 = _拉Finnhub单时段(
                股票代码,
                段起始.strftime("%Y-%m-%d"),
                当前.strftime("%Y-%m-%d"),
            )
            for 日期, 标题, 新闻id in 一批:
                if 新闻id in 已见id:
                    continue
                已见id.add(新闻id)
                结果.append((日期, 标题))
        except Exception as e:
            # 单段失败跳过，继续往前扫
            print(f"    [Finnhub] {段起始.strftime('%Y-%m-%d')} 段失败: {e}")
        当前 = 段起始 - datetime.timedelta(days=1)
        # 免费档 30次/秒，这里加个短间隔避免 429
        time.sleep(0.05)

    print(f"    [Finnhub] 共扫 {段数} 段，去重后 {len(结果)} 条新闻")
    return 结果, None


def _标注Finnhub情绪(新闻列表):
    """
    用本地 FinGPT 逐条标注英文新闻情绪（-1/0/+1），返回 [(date, sentiment)]。
    逐条较慢，打印进度。
    """
    import 情绪分析
    logger = 情绪分析._拿日志("回填")
    逐日 = {}
    for i, (日期, headline) in enumerate(新闻列表, 1):
        try:
            score = 情绪分析._用FinGPT分析(headline[:300], logger, 最多重试=2)
            逐日.setdefault(日期, []).append(score)
        except Exception as e:
            # 单条失败跳过（FinGPT 或 DeepSeek 都失败）
            continue
        if i % 10 == 0:
            print(f"    [Finnhub标注] 已处理 {i}/{len(新闻列表)} 条")
    结果 = []
    for 日期 in sorted(逐日.keys()):
        分数们 = 逐日[日期]
        结果.append((日期, round(sum(分数们) / len(分数们), 4)))
    return 结果


# ============================================================
# 合并 & 写盘
# ============================================================

def _合并写盘(股票代码, 新数据):
    """
    新数据: list[(date_str, sentiment)]
    与现有情绪 CSV 合并，按日期去重（新数据优先），写回。
    """
    文件夹 = os.path.join(数据目录, 股票代码)
    os.makedirs(文件夹, exist_ok=True)
    文件 = os.path.join(文件夹, f"{股票代码}_情绪数据.csv")

    新df = pd.DataFrame(新数据, columns=["date", "sentiment"])

    if os.path.exists(文件):
        old = pd.read_csv(文件)
        # 统一列名
        if 'date' in old.columns:
            combined = pd.concat([old, 新df]).drop_duplicates(subset=["date"], keep="last")
        else:
            combined = 新df
    else:
        combined = 新df

    # 保留其他可能存在的列（summary/engine/新闻条数），缺失填空
    for col in ["summary", "engine", "新闻条数"]:
        if col not in combined.columns:
            combined[col] = ""
    combined = combined.sort_values("date")
    combined.to_csv(文件, index=False)
    return len(combined)


def 回填单只(股票代码, 起始日期="2022-01-01"):
    """回填单只股票，打印进度，返回结果统计。"""
    结束日期 = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"回填 {股票代码} 真实历史情绪")
    print(f"{'='*60}")

    # Alpha Vantage 起始日期格式 YYYYMMDDTHHMM
    av_from = 起始日期.replace("-", "") + "T0000"
    av_to = 结束日期.replace("-", "") + "T2359"

    汇总 = {"股票": 股票代码, "Alpha天数": 0, "Finnhub天数": 0, "合并天数": 0}

    # 源1：Alpha Vantage
    print(f"[1/2] Alpha Vantage 自带情绪分 ...")
    alpha数据, err = _拉Alpha情绪(股票代码, av_from, av_to)
    if err:
        print(f"  ⚠️ {err}")
    else:
        print(f"  ✅ Alpha 拉到 {len(alpha数据)} 天情绪")
        汇总["Alpha天数"] = len(alpha数据)

    # 源2：Finnhub
    print(f"[2/2] Finnhub 历史新闻 + 本地 FinGPT 标注 ...")
    finnhub数据 = []
    if FINNHUB_KEY:
        # Finnhub 免费档只 1 年，起始日期取最近 1 年
        一年前 = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        f_from = max(起始日期, 一年前)
        新闻, err = _拉Finnhub新闻(股票代码, f_from, 结束日期)
        if err:
            print(f"  ⚠️ {err}")
        elif not 新闻:
            print(f"  ⚠️ Finnhub 无新闻返回")
        else:
            print(f"  ✅ Finnhub 拉到 {len(新闻)} 条新闻，开始标注（较慢）...")
            finnhub数据 = _标注Finnhub情绪(新闻)
            print(f"  ✅ Finnhub 标注完成 {len(finnhub数据)} 天情绪")
            汇总["Finnhub天数"] = len(finnhub数据)
    else:
        print(f"  ⚠️ 未配置 FINNHUB_API_KEY，跳过")

    # 合并
    全部 = alpha数据 + finnhub数据
    if not 全部:
        print(f"\n  ❌ 未拉到任何情绪数据，跳过写盘。")
        return 汇总

    # 去重：同一日期若两个源都有，取平均
    tmp = {}
    for 日期, score in 全部:
        tmp.setdefault(日期, []).append(score)
    合并 = [(d, round(sum(s) / len(s), 4)) for d, s in sorted(tmp.items())]

    天数 = _合并写盘(股票代码, 合并)
    汇总["合并天数"] = 天数
    print(f"\n  🎉 完成：共 {天数} 天真实情绪已写入 数据仓库/{股票代码}/{股票代码}_情绪数据.csv")
    return 汇总


def 主流程():
    if len(sys.argv) > 1:
        股票列表 = [s.strip().upper() for s in sys.argv[1].split(",") if s.strip()]
    else:
        import configparser
        c = configparser.ConfigParser()
        c.read("系统配置.properties", encoding="utf-8")
        股票列表 = [t.strip().upper() for t in c['DEFAULT'].get('tickers', 'TSLA').split(',') if t.strip()]

    if not ALPHA_KEY and not FINNHUB_KEY:
        print("⚠️ 未配置任何数据源 key！")
        print("  请先在 .env 填入：")
        print("    ALPHA_VANTAGE_API_KEY  (https://www.alphavantage.co/support/#api-key)")
        print("    FINNHUB_API_KEY        (https://finnhub.io/register)")
        return

    总汇总 = []
    for 股票 in 股票列表:
        try:
            总汇总.append(回填单只(股票))
        except Exception as e:
            print(f"  ❌ {股票} 回填失败: {e}")

    print(f"\n{'='*60}")
    print("回填汇总：")
    print(f"{'股票':<8}{'Alpha天数':<12}{'Finnhub天数':<14}{'合并天数':<10}")
    for s in 总汇总:
        print(f"{s['股票']:<8}{s['Alpha天数']:<12}{s['Finnhub天数']:<14}{s['合并天数']:<10}")
    print(f"{'='*60}")


if __name__ == "__main__":
    主流程()
