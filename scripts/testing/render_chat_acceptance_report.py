"""Render reviewed acceptance evidence as Markdown and standalone HTML."""
from __future__ import annotations

import argparse
import html
import json
import math
import statistics
from collections import Counter
from pathlib import Path


def percentile(values, q):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * q) - 1)] if ordered else 0


def render(folder, suite_path):
    suite = json.loads(suite_path.read_text())
    results = [json.loads(line) for line in (folder / "results.jsonl").read_text().splitlines()]
    review = json.loads((folder / "semantic-review.json").read_text())
    indexed = {(r["id"], r["repeat"]): r for r in results}
    expected = {(c["id"], n) for c in suite["cases"] for n in range(1, 4)}
    assert len(results) == 120 and set(indexed) == expected, "incomplete or duplicate samples"
    summaries = []
    functional = []
    for case in suite["cases"]:
        samples = []
        for repeat in range(1, 4):
            r = indexed[(case["id"], repeat)]
            judgement = review[case["id"]][str(repeat)]
            structural_failures = [k for k, v in r["structural_checks"].items() if not v]
            ok = not structural_failures and judgement["pass"]
            samples.append({"repeat": repeat, "pass": ok, "semantic": judgement,
                            "structural_failures": structural_failures, "elapsed_s": r["elapsed_s"]})
            if case.get("gating", True):
                functional.append(ok)
        summaries.append({"id": case["id"], "group": case["group"], "query": case["request"]["query"],
                          "gating": case.get("gating", True), "passed": sum(s["pass"] for s in samples), "samples": samples})
    counts = Counter("pass" if x["passed"] == 3 else "fail" for x in summaries if x["gating"])
    fast = [r["elapsed_s"] for r in results if not r["id"].startswith("F") and r["id"] != "O01"
            and r["http_status"] == 200 and str(r["response"].get("debug_info", {}).get("backend", "")).startswith("fast-chat")]
    record = [r["elapsed_s"] for r in results if r["id"].startswith("R")]
    overview = {"cases": len(summaries), "gating_cases": len(summaries)-1,
                "all_three_passed": counts["pass"], "failing_cases": counts["fail"], "observations": 1,
                "samples": 120, "gating_samples": len(functional), "passed_samples": sum(functional),
                "failed_samples": len(functional)-sum(functional),
                "record_median_s": round(statistics.median(record), 3),
                "record_p95_s": round(percentile(record, .95), 3),
                "fast_path_sample_count": len(fast), "fast_path_median_s": round(statistics.median(fast), 3),
                "fast_path_p95_s": round(percentile(fast, .95), 3), "fast_path_over_8s": sum(v > 8 for v in fast)}
    (folder / "summary.json").write_text(json.dumps({"overview": overview, "cases": summaries}, ensure_ascii=False, indent=2)+"\n")
    lines = ["# 语音助手 40 条用例执行报告", "", "测试日期：2026-10-04（北京时间）。每条用例重复 3 次，共 120 次请求。", "",
        f"39 条正式用例中，{counts['pass']} 条三次全部通过，{counts['fail']} 条存在失败；另有 1 条观察用例。正式样本 {sum(functional)}/{len(functional)} 通过。", "",
        "## 验证范围", "",
        "- 使用 Docker 生产镜像 `voice-assistant:local`（0.1.32）；`fast_chat.py` SHA256 与本地主干一致，见 metadata.json。",
        "- 请求经过真实 FastAPI `/v1/chat` 的认证、中间件和处理逻辑，使用 ASGITransport 在进程内调用；未经过公网反代、TCP 或客户端 UI。",
        "- 正常用例使用真实 Clef 分类、真实 ChatCompletion、独立真实 Harness 和 Memos 0.30.0；逐例重置合成备忘，并读回实际存储正文。",
        "- 仅 PocketBase 会话历史写入替换为隔离内存列表，故障例 F01–F04 注入指定异常/模型响应；未验证真实故障发生概率。",
        "- 固定模型时间为 2026-10-04 16:00:00 星期日。线上搜索结果依执行时来源，不能保证索引覆盖截至固定时间的最新内容。",
        "- 未验证麦克风录音、ASR、TTS 或目标应用实际回贴。",
        "- 首次隔离 Harness 未加载 CLIPROXYAPP_API_KEY，造成查询拆词失败；补齐与生产一致的该项凭据后，查询及后续用例全部重跑。报告只计入有效样本，记录类 48 个有效样本保留。", "",
        "## 延迟", "",
        f"- 记录类 48 次：中位数 {overview['record_median_s']} 秒，P95 {overview['record_p95_s']} 秒。",
        f"- 实际 fast-path 正常请求 {len(fast)} 次：中位数 {overview['fast_path_median_s']} 秒，P95 {overview['fast_path_p95_s']} 秒；{overview['fast_path_over_8s']} 次超过 8 秒。",
        "- 使用观测样本的 nearest-rank P95；计时包含 API 调用与模型/Memos 请求，不含 fixture 准备和验收读回。正常请求中路由错误但实际走 fast-path 的样本也保留在延迟统计。联网慢路径与注入故障另列实际耗时，不混入 fast-path 分位数。", "",
        "## 逐例结论", "", "| 用例 | 问句 | 通过次数 | 结论 / 语义审阅 |", "|---|---|---|---|"]
    for c in summaries:
        notes = list(dict.fromkeys(s["semantic"]["note"] for s in c["samples"]))
        verdict = "观察" if not c["gating"] else "通过" if c["passed"] == 3 else "失败"
        lines.append(f"| {c['id']} | {c['query']} | {c['passed']}/3 | {verdict}：{'；'.join(notes)} |")
    findings = json.loads((folder / "findings.json").read_text())
    lines += ["", "## 失败项与建议", ""]
    for finding in findings:
        lines += [f"### {finding["priority"]} {finding["title"]}（{", ".join(finding["cases"])}）", "",
                  finding["observed"], "", "证据：" + finding["cause_evidence"], "", "建议：" + finding["suggested_fix"], ""]
    lines += ["", "## 原始证据", "", "- `results.jsonl`：120 条响应、路由结果、模型完成事件、Memos 调用顺序及读回正文、Harness 工具事件和结构检查。外部搜索正文已替换为哈希与来源引用，来源元数据见 `search-sources.json`。",
              "- `semantic-review.json`：逐次人工语义审阅，独立于自动结构检查；示例响应不做逐字匹配。",
              "- `summary.json`：各用例及总体统计。",
              "- `metadata.json`：代码哈希与执行方式。",
              "- `cleanup.json`：清理后测试库记录数；容器及临时凭据清理结果见该文件的容器、网络、临时凭据清理字段。", "",
              "正式用例必须三次都通过才标记该用例通过。观察用例 O01 不计入正式通过率。", ""]
    (folder / "report.md").write_text("\n".join(lines))
    esc = html.escape
    cards = []
    for c in summaries:
        status = "观察" if not c["gating"] else "通过" if c["passed"] == 3 else "失败"
        details = []
        for sample in c["samples"]:
            r = indexed[(c["id"], sample["repeat"])]
            details.append(f"<details><summary>第 {sample['repeat']} 次 · {r['elapsed_s']} 秒 · HTTP {r['http_status']}</summary><p>{esc(sample['semantic']['note'])}</p><pre>{esc(json.dumps(r, ensure_ascii=False, indent=2))}</pre></details>")
        cards.append(f"<article data-status='{status}'><h2>{c['id']} · {status} · {c['passed']}/3</h2><p>{esc(c['query'])}</p>{''.join(details)}</article>")
    doc = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>语音助手验收报告</title><style>body{font:16px/1.6 system-ui;background:#f6f7fa;color:#182231;max-width:1100px;margin:36px auto;padding:0 20px}article{background:white;padding:20px;margin:18px 0;border-radius:12px;border-left:5px solid #239467}article[data-status=失败]{border-color:#d34444}article[data-status=观察]{border-color:#b68c21}summary,button{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#f2f4f7;padding:15px}button{padding:9px 16px;margin:5px;border:1px solid #bfc6d1;border-radius:7px}h2{font-size:19px}</style><h1>语音助手 40 条用例执行报告</h1>" + f"<p>每条 3 次，共 120 次。正式用例 {counts['pass']}/39 三次全部通过，{counts['fail']} 条失败，另有 1 条观察用例。</p><p>真实模型与独立真实 Memos / Harness；API 进程内验证，未测 ASR、TTS、实际回贴。历史持久化使用测试替身；4 类故障注入。记录 P95：{overview['record_p95_s']} 秒。</p>" + "<nav><button onclick='filterRows(\"全部\")'>全部</button><button onclick='filterRows(\"失败\")'>只看失败</button><button onclick='filterRows(\"通过\")'>只看通过</button><button onclick='filterRows(\"观察\")'>观察用例</button></nav>" + "".join(cards) + "<script>function filterRows(s){document.querySelectorAll('article').forEach(e=>e.hidden=s!=='全部'&&e.dataset.status!==s)}</script></html>"
    (folder / "report.html").write_text(doc)
    print(json.dumps(overview, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--suite", type=Path, default=Path("tests/scenarios/chat_acceptance.json"))
    args = parser.parse_args()
    render(args.folder, args.suite)
