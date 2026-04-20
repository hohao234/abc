import requests
import time
import random
import re
import os
import json
import urllib3
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 配置
SOURCE_URL = "https://spider.rer.de5.net/sub?sZXPG49v=m3u"
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_TXT = os.path.join(CURRENT_DIR, "traffic_report.txt")
OUTPUT_JSON = os.path.join(CURRENT_DIR, "traffic_summary.json")

TEST_DURATION = 8   # 每个样本测试 8 秒
SAMPLES_PER_IP = 2  # 每个 IP 随机抽样数
MAX_WORKERS = 5     # GitHub Actions 建议不要超过 5-10

def test_stream_traffic(name, url):
    """测试流媒体实际下载带宽"""
    ip_port = urlparse(url).netloc
    start_time = time.time()
    total_bytes = 0
    speeds_mbps = []
    headers = {'User-Agent': 'Mozilla/5.0 (Viera; rv:34.0) Gecko/20100101 Firefox/34.0'}
    
    try:
        # 1. 获取 m3u8
        r = requests.get(url, timeout=5, headers=headers, verify=False)
        if r.status_code != 200: return None
        
        # 2. 提取 ts 链接
        lines = r.text.split('\n')
        base_dir = url.rsplit('/', 1)[0]
        ts_lines = [l.strip() for l in lines if l.strip() and not l.startswith('#')]
        if not ts_lines: return None

        # 3. 循环测试
        while time.time() - start_time < TEST_DURATION:
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
                    if ts_duration > 0 and chunk_bytes > 10240: # 至少 10KB
                        mbps = (chunk_bytes * 8) / (ts_duration * 1024 * 1024)
                        speeds_mbps.append(mbps)
                except: continue
            time.sleep(0.5)
    except: return None

    test_time = time.time() - start_time
    if test_time > 0 and speeds_mbps:
        avg_speed = (total_bytes * 8) / (test_time * 1024 * 1024)
        return {
            "name": name, "ip_port": ip_port,
            "avg_mbps": round(avg_speed, 2), "stability": round(max(0, 1 - (max(speeds_mbps)-min(speeds_mbps))/avg_speed), 2)
        }
    return None

def main():
    print(f"🌐 正在从远程源获取数据...")
    try:
        r = requests.get(SOURCE_URL, timeout=10)
        r.raise_for_status()
        content = r.text
    except Exception as e:
        print(f"❌ 无法读取源: {e}")
        return

    # 分组逻辑
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

    # 抽样任务
    tasks = []
    for ip_port, urls in groups.items():
        samples = random.sample(urls, min(len(urls), SAMPLES_PER_IP))
        tasks.extend(samples)

    print(f"📊 发现 {len(groups)} 个服务器节点，共抽取 {len(tasks)} 个频道进行压测...")

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(test_stream_traffic, n, u) for n, u in tasks]
        for f in futures:
            res = f.result()
            if res: results.append(res)

    # 汇总
    summary = {}
    for res in results:
        ip = res['ip_port']
        if ip not in summary: summary[ip] = {"alive": 0, "speeds": []}
        summary[ip]["alive"] += 1
        summary[ip]["speeds"].append(res['avg_mbps'])

    final_summary = {}
    for ip, d in summary.items():
        final_summary[ip] = {
            "alive_count": d["alive"],
            "avg_mbps": round(sum(d["speeds"])/len(d["speeds"]), 2)
        }

    # 写入文件
    with open(OUTPUT_TXT, 'w', encoding='utf-8') as f:
        f.write(f"IPTV Traffic Report | {time.strftime('%Y-%m-%d %H:%M:%S')}\n" + "="*50 + "\n")
        for ip, s in final_summary.items():
            f.write(f"服务器: {ip:<20} | 有效系数: {s['alive_count']}/{SAMPLES_PER_IP} | 平均速度: {s['avg_mbps']} Mbps\n")

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(final_summary, f, indent=2)

    print(f"✅ 测速完成，结果已保存至 {OUTPUT_TXT}")

if __name__ == "__main__":
    main()
