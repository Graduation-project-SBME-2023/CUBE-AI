import asyncio
import aiohttp
import argparse
import time
import statistics
import json
import csv
from datetime import datetime

async def send_request(session, url, method, payload, idx, writer):
    try:
        start = time.time()
        start_ts = datetime.now().isoformat()

        if method.upper() == "POST":
            async with session.post(url, json=payload) as resp:
                text = await resp.text()
                elapsed = time.time() - start
                end_ts = datetime.now().isoformat()
                print(f"[{idx}] {method} {url} -> {resp.status}, {elapsed:.2f}s")

                # Log details
                writer.writerow([idx, method, url, resp.status, start_ts, end_ts, f"{elapsed:.4f}"])
                return resp.status, text, elapsed
        else:
            async with session.get(url, params=payload) as resp:
                text = await resp.text()
                elapsed = time.time() - start
                end_ts = datetime.now().isoformat()
                print(f"[{idx}] {method} {url} -> {resp.status}, {elapsed:.2f}s")

                writer.writerow([idx, method, url, resp.status, start_ts, end_ts, f"{elapsed:.4f}"])
                return resp.status, text, elapsed
    except Exception as e:
        print(f"[{idx}] Error: {e}")
        writer.writerow([idx, method, url, "ERROR", datetime.now().isoformat(), "", ""])
        return None, str(e), None

async def run_load_test(url, method, payload, total_requests, concurrency, writer):
    async with aiohttp.ClientSession() as session:
        tasks = []
        sem = asyncio.Semaphore(concurrency)

        async def bounded_request(i):
            async with sem:
                return await send_request(session, url, method, payload, i, writer)

        for i in range(total_requests):
            tasks.append(asyncio.create_task(bounded_request(i)))

        results = await asyncio.gather(*tasks)
        return results

def main():
    parser = argparse.ArgumentParser(description="MONAI Label Endpoint Load Tester")
    parser.add_argument("--url", type=str, required=True, help="Endpoint URL")
    parser.add_argument("--method", type=str, default="GET", choices=["GET", "POST"], help="HTTP method")
    parser.add_argument("--payload", type=str, default="{}", help="JSON payload (for POST/GET)")
    parser.add_argument("--requests", type=int, default=10, help="Total number of requests")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent requests")
    parser.add_argument("--log", type=str, default="results.csv", help="CSV log file")
    args = parser.parse_args()

    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError:
        print("❌ Invalid JSON payload")
        return

    with open(args.log, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["RequestID", "Method", "URL", "Status", "StartTime", "EndTime", "LatencySec"])

        start = time.time()
        results = asyncio.run(run_load_test(args.url, args.method, payload, args.requests, args.concurrency, writer))
        end = time.time()

    successes = [elapsed for status, _, elapsed in results if status == 200 and elapsed is not None]
    failures = len(results) - len(successes)

    # Stats
    avg_time = statistics.mean(successes) if successes else 0
    min_time = min(successes) if successes else 0
    max_time = max(successes) if successes else 0

    print("\n=== Load Test Summary ===")
    print(f"Total Requests: {args.requests}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Successes: {len(successes)}")
    print(f"Failures: {failures}")
    print(f"Elapsed Time: {end - start:.2f} seconds")
    print(f"Requests/sec: {args.requests / (end - start):.2f}")
    print(f"Avg Response Time: {avg_time:.2f}s")
    print(f"Min Response Time: {min_time:.2f}s")
    print(f"Max Response Time: {max_time:.2f}s")
    print(f"📂 Detailed log saved in: {args.log}")

if __name__ == "__main__":
    main()
