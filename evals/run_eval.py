"""评测脚本：在 gold set 上跑出可复现的指标。

用法：
    python -m evals.run_eval            # 打印结果
    python -m evals.run_eval --markdown # 输出 Markdown 表格（写文档用）

指标口径说明（重要，否则数字会被误读）：
- 规则召回：期望命中的规则中，实际被命中的比例。漏报在执法场景里代价最高。
- 规则精确：material findings（risk/mitigation 级）中被期望命中的比例。
  提示类（info）不计入分母，因为它们是流程提醒而非"发现"。
- 结论一致：系统给出的处置建议与人工标注是否同一档。
- 要素准确：对照人工标注的关键字段，抽取是否正确。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import engine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name="golden_cases"):
    with open(os.path.join(ROOT, "data", name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def material_fired(result):
    ids = []
    for r in result["risks"]:
        if r["severity"] in ("block", "warn"):
            ids.append(r["id"])
    for r in result["mitigations"]:
        ids.append(r["id"])
    return ids


def run(use_llm=False, set_name="golden_cases"):
    cases = load(set_name)
    tp = fn = fp = 0
    decision_ok = 0
    field_total = field_ok = 0
    latencies = []
    details = []

    for c in cases:
        r = engine.audit({"case_id": c["id"], "facts_text": c["facts_text"]}, use_llm=use_llm)
        fired = set(material_fired(r))
        expected = set(c["expect_rules"])
        hit = fired & expected
        tp += len(hit)
        fn += len(expected - fired)
        fp += len(fired - expected)

        d_ok = r["decision"] == c["expect_decision"]
        decision_ok += 1 if d_ok else 0

        f_detail = []
        for k, v in (c.get("expect_fields") or {}).items():
            field_total += 1
            actual = r["behavior"] if k == "behavior" else r["fields"].get(k)
            ok = str(actual) == str(v) or (isinstance(v, (int, float)) and actual == v)
            field_ok += 1 if ok else 0
            f_detail.append({"field": k, "expect": v, "actual": actual, "ok": ok})

        latencies.append(r["stats"]["elapsed_ms"])
        details.append({
            "id": c["id"],
            "focus": c["focus"],
            "expect": sorted(expected),
            "fired": sorted(fired),
            "missed": sorted(expected - fired),
            "spurious": sorted(fired - expected),
            "decision": r["decision"],
            "decision_expect": c["expect_decision"],
            "decision_ok": d_ok,
            "needs_human": r["stats"]["needs_human"],
            "fields": f_detail,
        })

    n = len(cases)
    summary = {
        "cases": n,
        "rule_precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
        "rule_recall": round(tp / (tp + fn), 3) if (tp + fn) else None,
        "decision_accuracy": round(decision_ok / n, 3),
        "field_accuracy": round(field_ok / field_total, 3) if field_total else None,
        "avg_latency_ms": round(sum(latencies) / n, 1),
        "tp": tp, "fp": fp, "fn": fn,
        "field_total": field_total, "field_ok": field_ok,
        "decision_fail": [d["id"] for d in details if not d["decision_ok"]],
        "rule_fail": [d["id"] for d in details if d["missed"] or d["spurious"]],
        "engine_mode": "llm+rules" if use_llm else "rules-only",
    }
    return summary, details


def to_markdown(summary, details):
    lines = []
    lines.append("| 用例 | 关注点 | 期望命中 | 实际命中 | 漏报 | 误报 | 结论 | 一致 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for d in details:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            d["id"], d["focus"],
            "、".join(d["expect"]) or "—",
            "、".join(d["fired"]) or "—",
            "、".join(d["missed"]) or "—",
            "、".join(d["spurious"]) or "—",
            "%s（期望 %s）" % (d["decision"], d["decision_expect"]),
            "✓" if d["decision_ok"] else "✗",
        ))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--set", default="golden_cases", help="golden_cases | stress_cases")
    args = ap.parse_args()
    summary, details = run(use_llm=args.llm, set_name=args.set)
    summary["set"] = args.set

    if args.markdown:
        print(to_markdown(summary, details))
        return

    print("=" * 56)
    print("度衡 DuHeng · 规则引擎评测  [%s]" % args.set)
    print("=" * 56)
    print("用例数            %d" % summary["cases"])
    print("规则召回率        %.1f%%  (TP=%d / 漏报=%d)" % (summary["rule_recall"] * 100, summary["tp"], summary["fn"]))
    print("规则精确率        %.1f%%  (误报=%d)" % (summary["rule_precision"] * 100, summary["fp"]))
    print("结论一致率        %.1f%%" % (summary["decision_accuracy"] * 100))
    print("要素抽取准确率    %.1f%%  (%d/%d)" % (summary["field_accuracy"] * 100, summary["field_ok"], summary["field_total"]))
    print("平均单案耗时      %.1f ms" % summary["avg_latency_ms"])
    print("引擎模式          %s" % summary["engine_mode"])
    if summary["decision_fail"]:
        print("结论不一致用例    %s" % ", ".join(summary["decision_fail"]))
    if summary["rule_fail"]:
        print("规则偏差用例      %s" % ", ".join(summary["rule_fail"]))
    print("-" * 56)
    for d in details:
        if d["missed"] or d["spurious"] or not d["decision_ok"]:
            print("%s %s" % (d["id"], d["focus"]))
            if d["missed"]:
                print("   漏报: %s" % "、".join(d["missed"]))
            if d["spurious"]:
                print("   误报: %s" % "、".join(d["spurious"]))
            if not d["decision_ok"]:
                print("   结论: 实际 %s / 期望 %s" % (d["decision"], d["decision_expect"]))
            bad = [f for f in d["fields"] if not f["ok"]]
            for f in bad:
                print("   字段 %s: 期望 %r 实际 %r" % (f["field"], f["expect"], f["actual"]))
    with open(os.path.join(ROOT, "data", "eval_result.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "details": details}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
