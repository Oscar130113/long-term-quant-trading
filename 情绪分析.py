"""
情绪分析.py —— 长线项目 · 新闻情绪模块（双引擎）

主引擎：本地 Ollama 的 FinGPT-MT（金融垂直模型，免费、隐私好、金融对口）
兜底引擎：DeepSeek 云端 API（FinGPT 失败/不可用时才启用，省额度）

FinGPT-MT 标准 prompt 模板（三分类输出）：
    Instruction: What is the sentiment of this news? Please choose an answer from {negative/neutral/positive}
    Input: <新闻内容>
    Answer:

输出统一映射为数值情绪：negative -> -1, neutral -> 0, positive -> +1
结果落盘 CSV，累积成历史情绪序列，供决策模块学习长期规律。
"""

import os
import json
import datetime
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

# ===== DeepSeek（兜底引擎）配置 =====
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ===== Ollama / FinGPT（主引擎）配置 =====
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
FINGPT_MODEL = os.getenv("FINGPT_MODEL", "modelscope.cn/second-state/FinGPT-MT-Llama-3-8B-LoRA-GGUF:latest")

# ⚠️ 关键：本地直连 ollama，绕过系统代理（终端挂 VPN 时会 502）
本地直连 = requests.Session()
本地直连.trust_env = False  # 忽略 HTTP_PROXY 等环境变量

数据目录 = "数据仓库"


def _拿日志(股票代码):
    os.makedirs("运行日志", exist_ok=True)
    logger = logging.getLogger(f"情绪_{股票代码}")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(f"运行日志/情绪_{股票代码}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
        logger.addHandler(fh)
    return logger


def _三分类映射(text):
    """把 FinGPT 输出的文本映射成数值情绪 -1/0/+1。"""
    t = text.strip().lower()
    if "positive" in t or "积极" in t or "正面" in t or "看多" in t:
        return 1.0
    if "negative" in t or "消极" in t or "负面" in t or "看空" in t:
        return -1.0
    return 0.0  # neutral 或无法识别


def _用FinGPT分析(新闻内容, logger, 最多重试=4):
    """
    调用本地 Ollama 的 FinGPT-MT 做情感三分类。
    带重试：模型冷加载期间可能 502，等待后重试。
    成功返回 -1/0/+1，多次失败抛异常。
    """
    prompt = (
        "Instruction: What is the sentiment of this news? "
        "Please choose an answer from {negative/neutral/positive}\n"
        f"Input: {新闻内容}\n"
        "Answer:"
    )
    payload = {
        "model": FINGPT_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": 0,  # 用完立即释放，避免和翻译模型同时驻留爆内存
        "options": {"temperature": 0.1, "num_predict": 20, "num_ctx": 512},  # 限制上下文，降低内存占用
    }

    import time
    last_err = None
    for 尝试 in range(最多重试):
        try:
            r = 本地直连.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
            if r.status_code != 200:
                raise Exception(f"Ollama HTTP {r.status_code}")
            answer = r.json().get("response", "").strip()
            logger.info(f"FinGPT 原始返回: {answer!r}")
            return _三分类映射(answer)
        except Exception as e:
            last_err = e
            等待 = [10, 20, 30, 45][min(尝试, 3)]
            logger.warning(f"FinGPT 第{尝试+1}次失败({e})，{等待}秒后重试...")
            time.sleep(等待)

    raise Exception(f"FinGPT 重试{最多重试}次仍失败: {last_err}")


def _用DeepSeek分析(新闻内容, logger):
    """
    调用 DeepSeek 云端 API（兜底）。
    成功返回 -1~+1 浮点数，失败抛异常。
    """
    if not DEEPSEEK_API_KEY:
        raise Exception("未配置 DEEPSEEK_API_KEY")

    prompt = (
        "你是金融新闻情绪分析师。判断下面这条新闻对该公司是利好还是利空。\n"
        "只输出一个数字：-1(利空)、0(中性)、+1(利好)。不要输出任何解释。\n\n"
        f"新闻：{新闻内容}"
    )
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "你只输出一个数字：-1、0 或 1。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 10,
    }
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(f"{DEEPSEEK_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=60)
    if r.status_code != 200:
        raise Exception(f"DeepSeek HTTP {r.status_code}")

    content = r.json()["choices"][0]["message"]["content"].strip()
    logger.info(f"DeepSeek 原始返回: {content!r}")
    # 提取数字
    import re
    m = re.search(r"-?\d+(\.\d+)?", content)
    if not m:
        raise Exception(f"DeepSeek 返回无法解析: {content}")
    val = float(m.group())
    return max(-1.0, min(1.0, val))


def 分析新闻情绪(股票代码="TSLA", 公司名="特斯拉", 新闻内容="",
                 新闻条数=8, 最近天数=7, 分析上限=5):
    """
    分析新闻情绪：FinGPT 优先，失败回退 DeepSeek。
    若不传新闻内容，则自动从新闻源拉取真实新闻 → 中译英 → FinGPT 判断。
    逐条分析后取平均，作为整体情绪。
    返回 dict: {"date", "sentiment", "summary", "engine", "新闻条数"}
    """
    logger = _拿日志(股票代码)

    # ===== 1. 获取新闻列表 =====
    新闻列表 = []
    if 新闻内容:
        # 手动传入的单条文本，当作一条新闻
        新闻列表 = [{"标题": 新闻内容, "内容": ""}]
    else:
        try:
            import 新闻源
            新闻列表 = 新闻源.拉取新闻(股票代码, 条数=新闻条数, 最近天数=最近天数)
            logger.info(f"从新闻源拉取到 {len(新闻列表)} 条真实新闻")
        except Exception as e:
            logger.warning(f"新闻源拉取失败({e})")

    if not 新闻列表:
        logger.warning("无可用新闻，跳过情绪分析")
        return None

    # ===== 2. 第一步：批量中译英（Qwen 只加载一次） =====
    import 翻译
    待翻译文本 = []
    for 新闻 in 新闻列表[:分析上限]:  # 最多分析 N 条，控制耗时
        标题 = 新闻.get("标题", "")
        内容 = 新闻.get("内容", "")
        原始文本 = 标题 if 标题 else 内容
        if 原始文本.strip():
            待翻译文本.append(原始文本.strip()[:200])

    if not 待翻译文本:
        logger.warning("无有效新闻文本")
        return None

    try:
        英文文本列表 = 翻译.批量中译英(待翻译文本)
    except Exception as e:
        logger.warning(f"批量翻译失败({e})，用原文")
        英文文本列表 = 待翻译文本

    # ===== 3. 第二步：批量 FinGPT 判断（FinGPT 只加载一次） =====
    分数列表 = []
    引擎 = "FinGPT"
    for 英文文本 in 英文文本列表:
        if not 英文文本.strip():
            continue
        try:
            单条分数 = _用FinGPT分析(英文文本, logger)
        except Exception as e:
            logger.warning(f"FinGPT 分析单条失败({e})，回退 DeepSeek...")
            try:
                单条分数 = _用DeepSeek分析(英文文本, logger)
                引擎 = "DeepSeek"
            except Exception as e2:
                logger.error(f"DeepSeek 兜底也失败: {e2}")
                continue
        分数列表.append(单条分数)

    if not 分数列表:
        logger.warning("所有新闻分析均失败")
        return None

    # ===== 3. 取平均作为整体情绪 =====
    平均情绪 = sum(分数列表) / len(分数列表)

    result = {
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "sentiment": round(平均情绪, 4),
        "summary": f"引擎={引擎}",
        "engine": 引擎,
        "新闻条数": len(分数列表),
    }
    logger.info(f"情绪分析完成: {result} (逐条分数={分数列表})")
    return result


def 保存情绪(股票代码, result):
    """把当次情绪结果追加到 CSV，累积成历史序列。"""
    if not result:
        return
    文件夹 = os.path.join(数据目录, 股票代码)
    os.makedirs(文件夹, exist_ok=True)
    文件 = os.path.join(文件夹, f"{股票代码}_情绪数据.csv")

    import pandas as pd
    new_row = pd.DataFrame([result])
    if os.path.exists(文件):
        old = pd.read_csv(文件)
        combined = pd.concat([old, new_row]).drop_duplicates(subset=["date"], keep="last")
    else:
        combined = new_row
    combined.to_csv(文件, index=False)


def 分析并保存(股票代码="TSLA", 公司名="特斯拉", 新闻内容="",
               新闻条数=8, 最近天数=7, 分析上限=5):
    """分析情绪并保存到 CSV（供数据更新模块调用）。"""
    res = 分析新闻情绪(股票代码, 公司名, 新闻内容, 新闻条数, 最近天数, 分析上限)
    if res:
        保存情绪(股票代码, res)
        print(f"  [成功] 情绪数据已更新: {股票代码} (sentiment={res['sentiment']}, 引擎={res['engine']})")
    else:
        print(f"  [失败] 情绪更新失败: {股票代码}")


if __name__ == "__main__":
    # 独立测试：python 情绪分析.py
    # 不传新闻内容，自动从新闻源拉取真实特斯拉新闻分析
    res = 分析新闻情绪("TSLA", "特斯拉")
    if res:
        print(f"情绪评分: {res['sentiment']} (引擎: {res['engine']}, 新闻{res['新闻条数']}条)")
        保存情绪("TSLA", res)
        print("已保存到数据仓库。")
    else:
        print("分析失败：FinGPT 和 DeepSeek 均不可用，或无新闻。请检查 ollama 服务。")
