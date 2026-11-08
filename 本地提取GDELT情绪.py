"""
本地提取GDELT情绪.py —— 从移动硬盘已有的 gkg 压缩包提取 TSLA 情绪

用途：GDELT 原始包已经下载到 /Volumes/A/GDELT原始（22250 个，364 天），
但之前因磁盘满导致情绪提取环节失败，情绪 CSV 里只有 1 天 GDELT 数据。
本脚本直接从本地 zip 重新提取情绪，无需重新联网下载。

输出：合并到 数据仓库/TSLA/TSLA_情绪数据.csv
"""

import os
import io
import zipfile
import pandas as pd
import concurrent.futures

原始目录 = "/Volumes/A/GDELT原始"
关键词 = ["tesla", "tsla", "elon musk"]


def 提取单个(路径):
    """从单个 gkg zip 提取 TSLA 情绪，返回 [(date_str, tone)]。"""
    try:
        with zipfile.ZipFile(路径) as z:
            名字 = z.namelist()[0]
            with z.open(名字) as f:
                文本 = f.read().decode('utf-8', errors='ignore')
    except Exception:
        return []
    结果 = []
    for 行 in 文本.split('\n'):
        if not 行.strip():
            continue
        列 = 行.split('\t')
        if len(列) < 8:
            continue
        日期 = 列[1][:8]
        标题 = (列[4] if len(列) > 4 else '') + ' ' + (列[5] if len(列) > 5 else '')
        标题 = 标题.lower()
        if not any(k in 标题 for k in 关键词):
            continue
        tone = None
        for 候选 in 列[6:]:
            try:
                tone = float(候选.split(',')[0])
                break
            except (ValueError, IndexError):
                continue
        if tone is not None:
            结果.append((日期, tone))
    return 结果


def 主流程():
    # 列出所有有效 zip
    文件列表 = []
    for f in os.listdir(原始目录):
        if f.endswith('.zip'):
            全路径 = os.path.join(原始目录, f)
            if os.path.getsize(全路径) > 0:
                文件列表.append(全路径)

    print(f"共 {len(文件列表)} 个有效 gkg 文件，开始并发提取...")

    所有结果 = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as 池:
        未来 = {池.submit(提取单个, p): p for p in 文件列表}
        完成 = 0
        for fut in concurrent.futures.as_completed(未来):
            try:
                r = fut.result()
                if r:
                    所有结果.extend(r)
            except Exception:
                pass
            完成 += 1
            if 完成 % 2000 == 0:
                print(f"  已处理 {完成}/{len(文件列表)}，累计 {len(所有结果)} 条情绪")

    print(f"提取完成，共 {len(所有结果)} 条 TSLA 情绪")

    if not 所有结果:
        print("未提取到数据")
        return

    # 按天聚合
    df = pd.DataFrame(所有结果, columns=["date", "tone"])
    df["date"] = df["date"].apply(lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}")
    df["sentiment"] = df["tone"] / 100.0
    每日 = df.groupby("date")["sentiment"].mean().round(4).reset_index()

    print(f"按天聚合后: {len(每日)} 天 GDELT 情绪")

    # 合并到情绪文件
    情绪文件 = "数据仓库/TSLA/TSLA_情绪数据.csv"
    if os.path.exists(情绪文件):
        old = pd.read_csv(情绪文件)
        # 去掉旧的 GDELT 行（重新提取）
        old = old[old.get('engine', '') != 'GDELT']
        combined = pd.concat([old, 每日.assign(engine="GDELT")]).sort_values("date")
    else:
        combined = 每日.assign(engine="GDELT")

    # 补齐列
    for col in ["summary", "新闻条数"]:
        if col not in combined.columns:
            combined[col] = ""

    combined.to_csv(情绪文件, index=False)
    print(f"✅ 已写入 {情绪文件}，共 {len(combined)} 天情绪（GDELT {len(每日)} 天）")


if __name__ == "__main__":
    主流程()
