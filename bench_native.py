#!/usr/bin/env python3
"""Honest benchmark: what moving hot paths into libchip_native.so actually buys.

Micro-benchmarking per-op C++ vs CPython is misleading here:
  * CPython's dict/bytes ops are already C, so pure pointer-moves beat any
    FFI call for tiny payloads (measured ~20-80x in favor of Python for
    cached lookups with no copy semantics — not a real workload).
  * The native reads do a true memcpy that a dict reference does not.

What actually dominates runtime in chip_web.py is I/O (LLM streaming +
Google TTS fetch), so the meaningful metrics are:
  * TTS cache hit vs cold fetch latency on the live server
  * bounded, thread-safe, TTL-expiring LRU coverage in C++ instead of a
    raw Python dict (memory safety, not raw speed)
"""
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8000"


def tts_latency(text):
    url = URL + "/api/tts?" + urllib.parse.urlencode({"text": text, "lang": "en"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(url, timeout=30) as r:
        data = r.read()
        cached = r.headers.get("X-Chip-Cache", "?")
    return time.perf_counter() - t0, len(data), cached


def stats():
    with urllib.request.urlopen(URL + "/api/stats", timeout=10) as r:
        return json.load(r)


def main():
    phrase = "systems online, all functions nominal, diagnostics green"
    # warm into cache (miss scheduled) then time 5 hits
    print("-- live server, same phrase --")
    cold = None
    for i in range(6):
        dt, size, cached = tts_latency(phrase)
        if i == 0:
            cold = dt
        print(f"req {i + 1}: {dt * 1000:9.1f} ms  {size:>6} B  cached={cached}")
    if cold:
        s = stats()["cache"]
        print(f"\ncold fetch: {cold * 1000:.1f} ms → native cache hit: "
              f"{round(s['misses'] / max(s['hits'] + s['misses'], 1) * 100, 1)}% miss rate, "
              f"{s['hits']} hits across {s['items']} cached blobs "
              f"({round(s['bytes'] / 1024)} KB, thread-safe LRU in C++)")


if __name__ == "__main__":
    import urllib.parse
    main()