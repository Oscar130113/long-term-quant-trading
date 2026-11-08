"""
翻译.py —— 长线项目 · 中译英翻译模块（基于 Ollama）

用本地 Ollama 的 qwen3:8b 把中文财经新闻翻译成英文，
供 FinGPT（英文情感模型）分析。全程本地、免费。

重要经验（踩坑记录）：
1. 必须用 /api/chat 接口（messages 格式），不能用 /api/generate（该模型在 generate 下返回空）。
2. 不要设 system 角色、不要设 temperature/num_predict 等 options（会触发返回空）。
3. 模型会偶尔在 prompt 里"复述/展开"，因此提示词要强调"只输出译文"。
4. keep_alive:0 会让模型每次冷加载(约10-20秒)，加载期间请求可能撞上 502，
   因此加了重试机制（遇到 502/连接错误，等待后重试）。
"""

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
翻译模型 = os.getenv("TRANSLATE_MODEL", "qwen3:8b")

# ⚠️ 关键：本地直连 ollama，绕过系统代理（终端挂 VPN 时，requests 会把
# localhost 请求也发给代理，代理无法转发本机请求就会返回 502 Bad Gateway）
本地直连 = requests.Session()
本地直连.trust_env = False  # 忽略 HTTP_PROXY 等环境变量


def _调用翻译(文本):
    """单次翻译调用，返回英文字符串。"""
    payload = {
        "model": 翻译模型,
        "messages": [
            {"role": "user", "content": f"把下面这句中文翻译成英文，只输出英文译文，不要解释、不要复述：{文本.strip()}"}
        ],
        "stream": False,
        "keep_alive": 0,  # 用完立即释放模型，避免和 FinGPT 同时驻留爆内存
        "options": {"num_ctx": 1024},  # 限制上下文，大幅降低内存占用（M1 Pro 内存紧张）
    }
    r = 本地直连.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=300)
    if r.status_code != 200:
        raise Exception(f"Ollama HTTP {r.status_code}")
    答案 = r.json().get("message", {}).get("content", "").strip()
    if not 答案:
        raise Exception("Ollama 返回空内容")
    # 清理：去掉可能的引号、markdown 粗体标记
    答案 = 答案.strip().strip('"').strip("'").replace("**", "")
    return 答案


def 中译英(文本):
    """
    把中文翻译成英文（基于 Ollama qwen3:8b），带重试。
    输入字符串，返回英文字符串；多次失败返回原文。
    """
    if not 文本 or not 文本.strip():
        return 文本

    for 尝试 in range(5):
        try:
            return _调用翻译(文本)
        except Exception as e:
            # 502 通常是模型冷加载中/内存紧张，等待后重试（10/20/30/45/60秒，共约3分钟）
            等待 = [10, 20, 30, 45, 60][尝试]
            print(f"  [翻译] 第{尝试+1}次失败({e})，{等待}秒后重试...")
            time.sleep(等待)

    print(f"  [翻译] 重试5次仍失败，返回原文")
    return 文本


def 批量中译英(文本列表):
    """批量翻译，返回等长英文列表。"""
    return [中译英(t) for t in 文本列表]


if __name__ == "__main__":
    # 独立测试：python 翻译.py
    样例 = [
        "特斯拉Optimus首批量产级机器人已在弗里蒙特工厂下线，规划年产能达100万台。",
        "特斯拉2026年三季度净利润同比下滑43%。",
    ]
    for 原文 in 样例:
        译文 = 中译英(原文)
        print(f"中文: {原文}")
        print(f"英文: {译文}")
        print()
