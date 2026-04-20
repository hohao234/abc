import requests
import time
import random
import re
import os
import json
import urllib3
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 配置
SOURCE_URL = "https://spider.rer.de5.net/sub?sZXPG49v=m3u"
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_TXT = os.path.join(CURRENT_DIR, "traffic_report.txt")
OUTPUT_JSON = os.path.join(CURRENT_DIR, "traffic_summary.json")

TEST_DURATION = 8   
SAMPLES_PER_IP = 2  
MAX_WORKERS = 5     

def test_stream_traffic(name, url):
    """测试流媒体并实时反馈进度"""
    ip_port = urlparse(url).netloc
    headers = {'User-Agent': 'Mozilla/5.0 (Viera; rv:34.0) Gecko/20100101 Firefox/34.0'}
    
    # 打印进度：开始测试
    print(f"  [尝试] {ip_port} -> {name}...")
    
    try:
        start_time = time.time()
        # 1. 获取 m3u8
        try:
            r = requests.get(url, timeout=4, headers=headers, verify=False)
            if r.status_code != 200:
                print(f"  [失败] {ip_port} | HTTP {r.status_code}")
                return {"ip_port": ip_port, "status": "Error", "code": r.status_code}
        except Exception as e:
            print(f"  [断开] {ip_port} | 连接超时")
            return {"ip_port": ip_port, "status": "Timeout"}
        
        # 2. 解析切片
        lines = r.text.split('\n')
        base_dir = url.rsplit('/', 1)[0]
        ts_lines = [l.strip() for l in lines if l.strip() and not l.startswith('#')]
        if not ts_lines:
            print(f"  [无效] {ip_port} | 未找到TS切片")
            return {"ip_port": ip_port, "status": "No_TS"}

        # 3. 压测
        total_bytes = 0
        speeds_mbps = []
        while time.time() - start_time < TEST_DURATION:
            target_ts = ts_lines[-2:] if len(ts_lines) > 2 else ts_lines
            for ts_path in target_ts:
                if time.time() - start_time > TEST_DURATION: break
                ts_url = ts_path if ts_path.startswith('http') else f"{base_dir}/{ts_path}"
                ts_start = time.time()
                try:
                    ts_r = requests.get(ts_url, timeout=4, headers=headers, stream=True, verify=False)
                    chunk_bytes = 0
                    for chunk in ts_r.iter_content(chunk_size=128*1024):
                        if chunk:
                            chunk_bytes += len(chunk)
                            total_bytes += len(chunk)
                            if time.time() - start_time > TEST_DURATION: break
                    
                    ts_duration = time.time() - ts_start
                    if ts_duration > 0 and chunk_bytes > 5120:
                        mbps = (chunk_bytes * 8) / (ts_duration * 1024 * 1024)
                        speeds_mbps.append(mbps)
                except: continue
        
        test_time = time.time() - start_time
        if speeds_mbps:
            avg_speed = (total_bytes * 8) / (test_time * 1024 * 1024)
            print(f"  [成功] {ip_port} | 速度: {avg_speed:.2f} Mbps")
            return {
                "name": name, "ip_port": ip_port, "status": "OK",
                "avg_mbps": round(avg_speed, 2)
            }
    except Exception as e:
        pass
    
    return {"ip_port": ip_port, "status": "Fail"}

def main():
    print(f"🚀 正在拉取远程源: {SOURCE_URL}")
    try:
        r = requests.get(SOURCE_URL, timeout=10)
        r.raise_for_status()
        content = r.text
    except Exception as e:
        print(f"❌ 无法读取源: {e}")
        return

    # 分组
    groups = {}
    lines = content.split('\n')
    for i in range(len(lines)):
        if lines[i].startswith('#EXTINF') and i+1 < len(lines):
            url = lines[i+1].strip()
            if url.startswith('http'):
                ip_port = urlparse(url).netloc
                if ip_port not in groups: groups[ip_port] = []
                name_match = re.search(r',(.+)$', lines[i])
                name = name_match.group(1).strip() if name_match else "Unknown"
                groups[ip_port].append((name, url))

    # 抽样
    tasks = []
    for ip_port, urls in groups.items():
        samples = random.sample(urls, min(len(urls), SAMPLES_PER_IP))
        tasks.extend(samples)

    print(f"\n📡 发现 {len(groups)} 个节点，计划测试 {len(tasks)} 个样本...\n" + "-"*50)

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_task = {executor.submit(test_stream_traffic, n, u): (n, u) for n, u in tasks}
        for future in as_completed(future_to_task):
            res = future.result()
            if res: results.append(res)

    # 汇总计算
    summary = {}
    for res in results:
        ip = res['ip_port']
        if ip not in summary: summary[ip] = {"ok": 0, "fail": 0, "speeds": []}
        if res['status'] == "OK":
            summary[ip]["ok"] += 1
            summary[ip]["speeds"].append(res['avg_mbps'])
        else:
            summary[ip]["fail"] += 1

    final_report = {}
    print("\n" + "="*50 + "\n📊 最终汇总结果:")
    for ip, d in summary.items():
        total_tried = d["ok"] + d["fail"]
        avg = round(sum(d["speeds"])/len(d["speeds"]), 2) if d["speeds"] else 0
        final_report[ip] = {
            "success_rate": f"{d['ok']}/{total_tried}",
            "avg_mbps": avg
        }
        status_icon = "✅" if avg > 2 else "⚠️" if avg > 0 else "❌"
        print(f"{status_icon} {ip:<22} | 成功:{d['ok']}/{total_tried} | 平均速度: {avg:>6} Mbps")

    # 保存文件
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump({"date": time.strftime('%Y-%m-%d %H:%M:%S'), "data": final_report}, f, indent=2)

    with open(OUTPUT_TXT, 'w', encoding='utf-8') as f:
        f.write(f"IPTV 测速汇总 | {time.strftime('%Y-%m-%d %H:%M:%S')}\n" + "-"*50 + "\n")
        for ip, s in final_report.items():
            f.write(f"{ip:<25} | {s['success_rate']:<5} | {s['avg_mbps']} Mbps\n")

    print(f"\n📄 报告已生成至 jar/ 目录下。")

if __name__ == "__main__":
    main()
