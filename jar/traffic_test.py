import requests
import time
import random
import re
import os
import json
import urllib3
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

# 1. 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 2. 路径重定位 (适配 LIVE/py 目录结构)
# 当前脚本在 LIVE/py/ 下
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# 向上跳一级到 LIVE/，再进入 hotels/ALL.m3u
SOURCE_M3U = os.path.join(CURRENT_DIR, "..", "hotels", "ALL.m3u")
# 报告保存在当前 py 文件夹
OUTPUT_TXT = os.path.join(CURRENT_DIR, "traffic_report.txt")
OUTPUT_JSON = os.path.join(CURRENT_DIR, "traffic_summary.json")

# --- 配置 ---
TEST_DURATION = 10  # 每个 ID 测试 10 秒（GitHub Action 环境建议缩短以防超时）
SAMPLES_PER_IP = 2  # 每个 IP 随机抽 2 个 ID 压测，兼顾速度与准确度
MAX_WORKERS = 5     # 并行线程数（GitHub 环境不建议设置过高）

def test_stream_traffic(name, url):
    """模拟播放并统计流量，计算 Mbps"""
    ip_port = urlparse(url).netloc
    start_time = time.time()
    total_bytes = 0
    speeds_mbps = []
    
    headers = {'User-Agent': 'Mozilla/5.0 (Viera; rv:34.0) Gecko/20100101 Firefox/34.0'}
    
    try:
        # 获取 m3u8 索引
        r = requests.get(url, timeout=5, headers=headers, verify=False)
        if r.status_code != 200: return None
        
        # 提取 .ts 切片
        lines = r.text.split('\n')
        base_dir = url.rsplit('/', 1)[0]
        ts_lines = [l.strip() for l in lines if l.strip() and not l.startswith('#')]
        if not ts_lines: return None

        # 循环下载切片
        while time.time() - start_time < TEST_DURATION:
            # 优先测试列表末尾的切片（更接近实时）
            target_ts = ts_lines[-2:] if len(ts_lines) > 2 else ts_lines
            for ts_path in target_ts:
                if time.time() - start_time > TEST_DURATION: break
                ts_url = ts_path if ts_path.startswith('http') else f"{base_dir}/{ts_path}"
                
                ts_start = time.time()
                try:
                    ts_r = requests.get(ts_url, timeout=5, headers=headers, stream=True, verify=False)
                    chunk_bytes = 0
                    for chunk in ts_r.iter_content(chunk_size=128*1024):
                        if chunk:
                            chunk_bytes += len(chunk)
                            total_bytes += len(chunk)
                            if time.time() - start_time > TEST_DURATION: break
                    
                    ts_duration = time.time() - ts_start
                    if ts_duration > 0 and chunk_bytes > 5120: # 至少有 5KB 数据才计入
                        mbps = (chunk_bytes * 8) / (ts_duration * 1024 * 1024)
                        speeds_mbps.append(mbps)
                except: continue
            time.sleep(0.5) 

    except:
        return None

    test_time = time.time() - start_time
    if test_time > 0 and speeds_mbps:
        avg_speed = (total_bytes * 8) / (test_time * 1024 * 1024)
        max_speed = max(speeds_mbps)
        min_speed = min(speeds_mbps)
        stability = 1 - ((max_speed - min_speed) / avg_speed) if avg_speed > 0 else 0
        stability = max(0, min(1, stability))
        
        return {
            "name": name, "ip_port": ip_port,
            "avg_mbps": round(avg_speed, 2), "max_mbps": round(max_speed, 2),
            "stability": round(stability, 2)
        }
    return None

def save_reports(results, group_summary):
    """保存结果"""
    with open(OUTPUT_TXT, 'w', encoding='utf-8') as f:
        f.write("="*75 + "\n")
        f.write(f"📡 IPTV 酒店源测速报告 | 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*75 + "\n")
        f.write(f"{'服务器 (IP:Port)':<25} | {'频道':<15} | {'速度':<12} | {'稳定性'}\n")
        f.write("-" * 75 + "\n")
        for res in results:
            f.write(f"{res['ip_port']:<25} | {res['name'][:12]:<15} | {res['avg_mbps']:>6} Mbps | {res['stability']*100:>3.0f}%\n")
        
        f.write("\n📊 综合汇总 (Summary):\n")
        for ip, summ in group_summary.items():
            f.write(f"{ip:<25} | 有效频道:{summ['alive_count']} | 平均:{summ['avg_mbps']:>5} Mbps\n")

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump({"summary": group_summary, "details": results}, f, ensure_ascii=False, indent=2)

def main():
    print(f"🚀 开始在 LIVE 仓库测速...")
    print(f"📂 目标源文件: {os.path.abspath(SOURCE_M3U)}")
    
    if not os.path.exists(SOURCE_M3U):
        print(f"❌ 错误: 找不到源文件 {SOURCE_M3U}，请检查目录结构。")
        return

    with open(SOURCE_M3U, 'r', encoding='utf-8') as f:
        content = f.read()

    # 解析 M3U
    groups = {}
    lines = content.split('\n')
    for i in range(len(lines)):
        if lines[i].startswith('#EXTINF') and i+1 < len(lines):
            url = lines[i+1].strip()
            if url.startswith('http'):
                ip_port = urlparse(url).netloc
                if ip_port not in groups: groups[ip_port] = []
                name = re.search(r',(.+)$', lines[i]).group(1).strip() if ',' in lines[i] else "Unknown"
                groups[ip_port].append((name, url))

    # 抽样
    tasks = []
    for ip_port, urls in groups.items():
        samples = random.sample(urls, min(len(urls), SAMPLES_PER_IP))
        tasks.extend(samples)

    print(f"📡 识别到 {len(groups)} 个 IP 源，准备测试 {len(tasks)} 个样本...")

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(test_stream_traffic, n, u) for n, u in tasks]
        for future in futures:
            res = future.result()
            if res: results.append(res)

    # 汇总
    group_summary = {}
    for res in results:
        ip = res['ip_port']
        if ip not in group_summary:
            group_summary[ip] = {"alive_count": 0, "speeds": [], "max_mbps": 0}
        s = group_summary[ip]
        s["alive_count"] += 1
        s["speeds"].append(res['avg_mbps'])
        s["max_mbps"] = max(s["max_mbps"], res['max_mbps'])

    for ip, data in group_summary.items():
        data["avg_mbps"] = round(sum(data["speeds"]) / len(data["speeds"]), 2)
        del data["speeds"]

    save_reports(results, group_summary)
    print(f"✅ 测速完成！\n📄 文本报告: {OUTPUT_TXT}\n📦 JSON汇总: {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
