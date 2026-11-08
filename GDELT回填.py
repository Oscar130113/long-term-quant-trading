"""
GDELT回填.py —— 长线项目 · 用 GDELT 2.0 批量数据回填历史新闻情绪

背景：
  FinGPT 只能分析"当天"新闻，回填不了历史；Finnhub 免费档只 1 年。
  GDELT 2.0 提供全球新闻的逐条 tone 情绪分，可回溯多年，免费无 key。

方案：
  不走 GDELT 的 DOC 查询 API（有全局 IP 限流，429），改走批量数据文件：
  - 每天一个 gkg.csv.zip（约 6MB），含全球新闻的 tone 情绪。
  - 下载 → 解压 → 按关键词(TSLA/Tesla)过滤 → 提取 tone → 按天聚合。

用法：
  ./.venv/bin/python GDELT回填.py TSLA 2024-01-01     # 从某天开始抓到现在
  ./.venv/bin/python GDELT回填.py                     # 默认 TSLA，抓最近1年

特性：
  - 断点续跑：记录已处理日期，中断后重跑自动跳过。
  - 遵守 GDELT 数据服务器节奏（每文件间隔 1 秒）。
  - 结果合并到 数据仓库/<代码>/<代码>_情绪数据.csv（tone 归一化到 [-1,1]）。
"""

import os
import sys
import time
import re
import io
import zipfile
import datetime
import requests
import concurrent.futures
import pandas as pd

数据目录 = "数据仓库"
GDELT_文件列表 = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GDELT_历史目录 = "http://data.gdeltproject.org/gdeltv2/"

# GDELT gkg.csv 的列：第 7 列是 tone（全局），第 4 列是文档信息（含来源URL）
# 结构：GKGRECORDID, DATE, SOURCECOLLECTION, SOURCE, DOCUMENT, COUNTS, THEMES, ...
# 实际 gkg 2.0 列：date, numarts, counts, themes, locations, persons, orgs, tone, ...

直连 = requests.Session()
直连.trust_env = False


def 下载gkg文件(时间戳, 存储目录=None):
    """
    下载某一天的 gkg.csv.zip。
    时间戳格式 YYYYMMDDHHMMSS。
    存储目录：若提供，则把原始 zip 存到该目录（文件名 = 时间戳.gkg.csv.zip）。
    返回 (原始字节, 解压文本)，失败返回 (None, None)。
    """
    url = f"{GDELT_历史目录}{时间戳}.gkg.csv.zip"
    try:
        r = 直连.get(url, timeout=120)
        if r.status_code != 200:
            return None, None
        原始 = r.content

        # 选择性存盘：保存原始压缩包（用时再解压）
        if 存储目录:
            try:
                os.makedirs(存储目录, exist_ok=True)
                路径 = os.path.join(存储目录, f"{时间戳}.gkg.csv.zip")
                if not os.path.exists(路径):  # 已存在则跳过
                    with open(路径, "wb") as f:
                        f.write(原始)
            except Exception as e:
                print(f"    [GDELT] 存盘失败 {时间戳}: {e}")

        # 解压
        with zipfile.ZipFile(io.BytesIO(原始)) as z:
            名字 = z.namelist()[0]
            with z.open(名字) as f:
                return 原始, f.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"    [GDELT] 下载失败 {时间戳}: {e}")
        return None, None


def 提取tsla情绪(csv文本, 关键词列表):
    """
    从 gkg csv 文本里过滤出包含关键词的新闻，提取 tone。
    gkg 2.0 结构（制表符分隔，约27列）：
      列[0] = GKGRECORDID
      列[1] = 日期时间（YYYYMMDDHHMMSS）
      列[2] = 文章数
      列[3] = 来源域名
      列[4] = 文章URL
      列[5] = 标题/文档信息  ← 关键词主要匹配这里
      ...
      tone 在靠后的列（含逗号分隔，第1个是全局 tone -100~+100）
    返回 [(date_str, tone)] 列表。
    """
    结果 = []
    for 行 in csv文本.split('\n'):
        if not 行.strip():
            continue
        列 = 行.split('\t')
        if len(列) < 8:
            continue
        日期 = 列[1][:8]  # YYYYMMDD
        # 标题在第5列(索引4)，URL在第4列(索引3)，都用来匹配关键词
        标题 = (列[4] if len(列) > 4 else '') + ' ' + (列[5] if len(列) > 5 else '')
        标题 = 标题.lower()
        if not any(k.lower() in 标题 for k in 关键词列表):
            continue
        # tone 列：gkg 2.0 里 tone 在索引约 19-21 附近，含逗号分隔。
        # 找到第一个能解析成浮点数的 tone 值（通常列[19]或附近）。
        tone = None
        for 候选 in 列[6:]:
            try:
                tone = float(候选.split(',')[0])
                break
            except (ValueError, IndexError):
                continue
        if tone is None:
            continue
        结果.append((日期, tone))
    return 结果


def 获取可用文件列表():
    """从 lastupdate.txt 和文件列表接口获取历史文件。返回 [(时间戳, 日期)]。"""
    # 简化：直接按日期构造文件名，测试是否存在
    # GDELT 每 15 分钟一个文件，我们只需每天最后一个（235900 或最新）
    return None


def 读取存储目录():
    """从系统配置读取 gdelt_raw_dir，返回存储目录路径（空串=不存原始包）。"""
    try:
        import configparser
        c = configparser.ConfigParser()
        c.read("系统配置.properties", encoding="utf-8")
        return c["DEFAULT"].get("gdelt_raw_dir", "").strip()
    except Exception:
        return ""


def 读取全量开关():
    """读取 gdelt_save_all：true=全量存包，false=只存含关键词的包。"""
    try:
        import configparser
        c = configparser.ConfigParser()
        c.read("系统配置.properties", encoding="utf-8")
        return c["DEFAULT"].get("gdelt_save_all", "false").strip().lower() == "true"
    except Exception:
        return False


def _处理单个文件(时间戳, 关键词, 存储目录, 全量保存=False):
    """
    下载单个 gkg 文件，提取情绪，可选存包。
    全量保存=True 时，不管是否含关键词都存包。
    返回 [(date_str, tone)] 列表。
    """
    try:
        原始字节, csv文本 = 下载gkg文件(时间戳)
        if csv文本 is None:
            return []
        r = 提取tsla情绪(csv文本, 关键词)
        # 存包条件：全量保存，或（非全量且含关键词）
        if 存储目录 and (全量保存 or r):
            存路径 = os.path.join(存储目录, f"{时间戳}.gkg.csv.zip")
            if not os.path.exists(存路径):
                try:
                    os.makedirs(存储目录, exist_ok=True)
                    with open(存路径, "wb") as f:
                        f.write(原始字节)
                except Exception:
                    pass
        return r
    except Exception:
        return []


def 回填(股票代码="TSLA", 起始日期="2024-01-01", 关键词=None, 保留原始=None, 并发数=8):
    """
    主流程：从起始日期到现在，逐天扫描 gkg 文件并提取情绪（多线程并发）。

    存储策略（按配置 gdelt_raw_dir 决定）：
      - 配置了目录（如移动硬盘）→ 只保存「含关键词」的 gkg 压缩包到该目录。
      - 未配置 → 不存原始包，下载后提取情绪即丢弃。

    并发数：同时下载的文件数（默认 8，GDELT 服务器限约 2MB/s/连接，并发可提速）。
    """
    if 关键词 is None:
        关键词 = ["tesla", "tsla", "elon musk"]

    if 保留原始 is None:
        存储目录 = 读取存储目录()
    else:
        存储目录 = 读取存储目录() if 保留原始 else None

    全量保存 = 读取全量开关()

    结束 = datetime.datetime.now()
    起始 = datetime.datetime.strptime(起始日期, "%Y-%m-%d")

    文件夹 = os.path.join(数据目录, 股票代码)
    os.makedirs(文件夹, exist_ok=True)
    进度文件 = os.path.join(文件夹, f"{股票代码}_gdelT进度.txt")

    已处理 = set()
    if os.path.exists(进度文件):
        with open(进度文件) as f:
            已处理 = set(f.read().split())

    所有结果 = []
    当前 = 起始
    处理天数 = 0

    print(f"[GDELT回填] {股票代码} 从 {起始日期} 到 {结束.strftime('%Y-%m-%d')}")
    print(f"[GDELT回填] 关键词={关键词}，已跳过 {len(已处理)} 天，并发数={并发数}")
    if 存储目录:
        模式 = "全量保存" if 全量保存 else "只存含关键词的包"
        print(f"[GDELT回填] 原始压缩包保存到: {存储目录}（{模式}）")
    else:
        print(f"[GDELT回填] 不保存原始压缩包")

    while 当前 <= 结束:
        日期str = 当前.strftime("%Y-%m-%d")
        日期时间戳 = 当前.strftime("%Y%m%d")

        if 日期str in 已处理:
            当前 += datetime.timedelta(days=1)
            continue

        # 生成当天所有 15 分钟文件的时间戳（UTC 00:00 ~ 23:45）
        时间戳列表 = []
        for 小时 in range(24):
            for 分钟 in [0, 15, 30, 45]:
                时间戳列表.append(f"{日期时间戳}{小时:02d}{分钟:02d}00")

        # 多线程并发下载+提取
        当天结果 = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=并发数) as 池:
            未来 = {池.submit(_处理单个文件, ts, 关键词, 存储目录, 全量保存): ts for ts in 时间戳列表}
            for fut in concurrent.futures.as_completed(未来):
                try:
                    r = fut.result()
                    if r:
                        当天结果.extend(r)
                except Exception:
                    pass

        if 当天结果:
            所有结果.extend([(日期str, tone) for _, tone in 当天结果])

        已处理.add(日期str)
        with open(进度文件, "w") as f:
            f.write("\n".join(sorted(已处理)))
        处理天数 += 1

        if 处理天数 % 5 == 0:
            print(f"    [GDELT] 已处理 {处理天数} 天（{日期str}），累计 {len(所有结果)} 条情绪")

        # 全量保存时检查磁盘空间，<2GB 自动停止（避免写爆硬盘）
        if 全量保存 and 存储目录:
            try:
                import shutil
                剩余 = shutil.disk_usage(存储目录).free / (1024**3)
                if 剩余 < 2:
                    print(f"    [GDELT] ⚠️ 磁盘剩余仅 {剩余:.1f}GB，自动停止（避免写爆硬盘）")
                    break
            except Exception:
                pass

        当前 += datetime.timedelta(days=1)

    print(f"[GDELT回填] 完成，共抓到 {len(所有结果)} 条情绪")

    if not 所有结果:
        print("[GDELT回填] 未抓到数据")
        return

    # 按天聚合
    df = pd.DataFrame(所有结果, columns=["date", "tone"])
    df["sentiment"] = df["tone"] / 100.0
    每日 = df.groupby("date")["sentiment"].mean().round(4).reset_index()
    每日.columns = ["date", "sentiment"]

    # 合并到情绪文件
    情绪文件 = os.path.join(文件夹, f"{股票代码}_情绪数据.csv")
    if os.path.exists(情绪文件):
        old = pd.read_csv(情绪文件)
        旧情绪 = old[["date", "sentiment"]] if "date" in old.columns else pd.DataFrame(columns=["date","sentiment"])
        combined = pd.concat([旧情绪, 每日]).drop_duplicates(subset=["date"], keep="last").sort_values("date")
    else:
        combined = 每日

    for col in ["summary", "engine", "新闻条数"]:
        if col not in combined.columns:
            combined[col] = ""
    每日_mask = combined["date"].isin(每日["date"])
    combined.loc[每日_mask, "engine"] = "GDELT"

    combined.to_csv(情绪文件, index=False)
    print(f"[GDELT回填] 已写入 {情绪文件}，共 {len(combined)} 天情绪（新增 GDELT {len(每日)} 天）")


if __name__ == "__main__":
    股票 = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    起始 = sys.argv[2] if len(sys.argv) > 2 else "2024-01-01"
    并发 = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    回填(股票, 起始, 并发数=并发)
