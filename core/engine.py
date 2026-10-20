"""四层审查引擎。

分层不是为了好看，而是为了对齐执法实务的思维顺序，也让「问题出在哪一层」
可以被定位：程序瑕疵可以补正，事实不清只能退回，法律适用错误可能被撤销，
裁量失当则是复议诉讼的高发点。四类问题的处置方式完全不同，混在一起报给
执法人员是没有用的。
"""

import json
import os
import time
from datetime import datetime

from . import expr, extract, llm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 这些事项属于「本应记载于案卷」的否定性事实。案卷未记载即推定不存在，
# 但推定结果会显式返回，方便复核人员一眼看出哪些结论建立在推定之上。
DOCKET_NEGATIVE_PRESUMPTION = {
    "prior_fine_same_act": False,
    "duplicate_record": False,
    "aggravating_factors": [],
    "mitigating_factors": [],
    "subjective_fault_excused": False,
    "minor_violation": False,
    "correction_order_issued": False,
    "law_version_current": True,
}

# 要素抽取失败的线索词。「文书里没写」和「系统没看懂」是两种完全不同的缺陷：
# 前者要退回补正，后者只需人工核对一眼。用线索词把两者区分开，避免把抽取缺陷
# 报成法律缺陷——这是压力测试暴露出来的最严重的一类误报。
FIELD_HINTS = {
    "violation_time": ["时", "日", "点"],
    "location": ["路", "街", "大道", "路口", "交叉口"],
    "plate": ["号牌", "车牌", "桂", "粤", "京"],
    "speed_limit": ["限速"],
    "measured_speed": ["时速", "速度", "千米", "km", "KM"],
}

_RULESET = None
_BEHAVIORS = None
_PROFILES = None


def _load(name):
    with open(os.path.join(ROOT, "rules", name), encoding="utf-8") as f:
        return json.load(f)


def ruleset():
    global _RULESET
    if _RULESET is None:
        _RULESET = _load("audit_rules.json")
    return _RULESET


def behaviors():
    global _BEHAVIORS
    if _BEHAVIORS is None:
        _BEHAVIORS = _load("behaviors.json")["behaviors"]
    return _BEHAVIORS


def region_profile(key="GX-2026"):
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = _load("region_profiles.json")["profiles"]
    return _PROFILES.get(key) or list(_PROFILES.values())[0]


def _parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


def _days_between(a, b):
    da, db = _parse_dt(a), _parse_dt(b)
    if not da or not db:
        return None
    return (db - da).days


def _fmt(value, ctx):
    if value is None:
        return ""
    # 文案里出现布尔值时用「是/否」，避免把 Python 字面量直接暴露给执法人员
    fmt_ctx = {
        k: ("是" if v is True else "否" if v is False else v)
        for k, v in ctx.items() if not isinstance(v, dict)
    }
    try:
        return str(value).format(**fmt_ctx)
    except (KeyError, IndexError, ValueError):
        return str(value)


def _resolve_band(bh, ctx):
    for band in bh.get("fine_bands", []) or []:
        if expr.resolve(band.get("when", {"op": "always"}), ctx) is True:
            return band
    return None


def audit(case, use_llm=True, profile_key="GX-2026"):
    started = time.perf_counter()
    text = case.get("facts_text", "") or ""
    review_date = case.get("review_date") or datetime.now().strftime("%Y-%m-%d")
    case_id = case.get("case_id") or "CASE-" + datetime.now().strftime("%Y%m%d%H%M%S")

    # 1) 事实抽取：规则抽取打底，LLM 增强（若配置）
    extracted = extract.extract_from_text(text)
    llm_used = False
    if use_llm and llm.available():
        llm_facts, _raw = llm.extract_facts(text)
        if llm_facts:
            llm_used = True
            for k, v in llm_facts.items():
                if k not in extracted or extracted[k] in (None, "", []):
                    extracted[k] = v
    fields, conflicts = extract.merge(case.get("structured"), extracted)

    behavior = fields.get("behavior") or extract.classify_behavior(text)
    bh = behaviors().get(behavior) or behaviors()["unknown"]
    prof = region_profile(profile_key)

    # 2) 组装上下文（扁平化，便于规则模板引用）
    ctx = {
        "case_id": case_id,
        "source": case.get("source") or "非现场（电子技术监控）",
        "behavior": behavior,
        "behavior_label": bh.get("label"),
        "agency": case.get("agency") or "未载明",
        "review_date": review_date,
        "owner_notice_status": "已向机动车所有人送达告知" if fields.get("owner_notified") else "材料中未见向机动车所有人送达告知的记录",
        "prior_fine_ref": fields.get("prior_fine_ref") or "见关联案件",
    }
    for k, v in fields.items():
        ctx[k] = v
    for k, v in prof.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            ctx[k] = v

    ctx["days_since_violation"] = _days_between(fields.get("violation_time"), review_date)
    ctx["days_collect_to_entry"] = _days_between(fields.get("collect_time"), fields.get("entry_time"))
    ctx["days_collect_to_notice"] = _days_between(fields.get("collect_time"), fields.get("notice_time"))

    if fields.get("speed_limit") and fields.get("measured_speed"):
        try:
            limit = float(fields["speed_limit"])
            ctx["overspeed_ratio"] = round((float(fields["measured_speed"]) - limit) / limit, 4)
        except (TypeError, ValueError, ZeroDivisionError):
            ctx["overspeed_ratio"] = None

    # 违法事实与处罚种类的基本推定：文书中出现罚款金额，即视为拟处罚种类含罚款。
    # 这是从文书本身可推得的信息，不属于「猜测」。
    if ctx.get("penalty_type") is None and ctx.get("proposed_fine") is not None:
        ctx["penalty_type"] = "罚款"

    # 案卷否定推定：这几类事项本应记载于案卷，案卷未记载即推定为「无」，
    # 但推定的字段会随结果一起返回并在留痕中标明，避免把假设伪装成事实。
    presumed = []
    for key, default in DOCKET_NEGATIVE_PRESUMPTION.items():
        if ctx.get(key) is None:
            ctx[key] = default
            presumed.append(key)

    # 先把派生量挂到 ctx.derived，因为档位规则的 when 依赖超速比例
    ctx["derived"] = {
        "days_since_violation": ctx.get("days_since_violation"),
        "days_collect_to_entry": ctx.get("days_collect_to_entry"),
        "days_collect_to_notice": ctx.get("days_collect_to_notice"),
        "overspeed_ratio": ctx.get("overspeed_ratio"),
    }

    band = _resolve_band(bh, ctx)
    ctx["fine_min"] = band.get("min") if band else None
    ctx["fine_max"] = band.get("max") if band else None
    ctx["fine_article"] = band.get("article") if band else "（未匹配到处罚条款）"
    ctx["band_may_revoke"] = band.get("may_revoke_license") if band else None
    ctx["band_note"] = band.get("note") if band else None
    # 定性是否存在，取决于「该行为有没有对应条款」，不取决于档位能否算出来。
    # 否则一旦速度数值抽取失败，就会被误报成「定性依据缺失」——把抽取缺陷伪装成法律缺陷。
    ctx["article_matched"] = bool(bh.get("fine_bands"))
    ctx["derived"]["fine_min"] = ctx["fine_min"]
    ctx["derived"]["fine_max"] = ctx["fine_max"]

    if isinstance(ctx.get("mitigating_factors"), list):
        ctx["mitigating_factors"] = "、".join(ctx["mitigating_factors"])

    # 3) 逐条执行规则
    required_fields = bh.get("required_fields", [])
    missing_required = [f for f in required_fields if not ctx.get(f)]
    suspect_fields = [f for f in missing_required if any(k in text for k in FIELD_HINTS.get(f, []))]
    ctx["suspect_fields"] = suspect_fields

    results = []
    for rule in ruleset()["rules"]:
        req_fields = []
        check = rule.get("check", "expr")
        if rule.get("applies_to") and behavior not in rule["applies_to"]:
            continue

        if check == "missing":
            req_fields = list(dict.fromkeys(list(rule.get("fields", [])) + list(bh.get("required_fields", []))))
            missing = [f for f in req_fields if not ctx.get(f)]
            state = True if missing else False
            ctx["missing_fields"] = "、".join(missing)
            ctx["missing_count"] = len(missing)
        elif check == "band":
            value = ctx.get(rule.get("field"))
            if value is None or ctx.get("fine_min") is None:
                state = None
            else:
                state = value < ctx["fine_min"] or value > ctx["fine_max"]
        elif check == "nonempty":
            value = ctx.get(rule.get("field"))
            state = None if value is None else len(value) > 0
        else:
            node = rule.get("when", {"op": "always"})
            state = expr.resolve(node, ctx)
            if state is None:
                missing_refs = [p for p in expr.referenced_fields(node) if expr.get_path(ctx, p) is None]
                if not missing_refs:
                    state = False

        if state is True:
            status = "fired"
        elif state is None:
            status = "insufficient"
        else:
            status = "pass"

        severity = rule.get("severity", "warn")
        finding = _fmt(rule.get("finding"), ctx)
        if (status == "fired" and rule.get("downgrade_if_suspect")
                and check == "missing" and missing and set(missing) <= set(suspect_fields)):
            severity = "warn"
            finding += "其中「%s」在文书中似有表述，疑为系统识别失败而非文书缺项，请人工核对。" % "、".join(missing)

        results.append({
            "id": rule["id"],
            "layer": rule["layer"],
            "title": rule["title"],
            "kind": rule.get("kind", "risk"),
            "severity": severity,
            "status": status,
            "finding": finding,
            "suggestion": _fmt(rule.get("suggestion"), ctx),
            "basis": rule.get("basis", []),
            "needs_human": bool(rule.get("needs_human")),
        })

    layer_names = {l["id"]: l["name"] for l in ruleset()["layers"]}
    layer_desc = {l["id"]: l["desc"] for l in ruleset()["layers"]}
    for r in results:
        r["layer_name"] = layer_names.get(r["layer"], "")
        r["layer_desc"] = layer_desc.get(r["layer"], "")

    risks = [r for r in results if r["kind"] == "risk" and r["status"] == "fired"]
    risks.sort(key=lambda r: ({"block": 0, "warn": 1, "notice": 2}.get(r["severity"], 3), r["layer"]))
    mitigations = [r for r in results if r["kind"] == "mitigation" and r["status"] == "fired"]
    infos = [r for r in results if r["kind"] == "info" and r["status"] == "fired"]
    pending = [r for r in results if r["status"] == "insufficient"]
    passed = [r for r in results if r["status"] == "pass"]

    blocking = [r for r in risks if r["severity"] == "block"]
    warning = [r for r in risks if r["severity"] == "warn"]

    if blocking:
        code, label, note = "RETURN", "退回补正，暂不宜作出处罚决定", "存在可能导致决定被撤销的阻断性问题，应先补正或重新调查。"
    elif mitigations:
        code, label, note = "DISCRETION", "可裁量不予处罚或从轻，需人工确认", "命中法定不予处罚或从轻减轻情形的初筛条件，最终裁量权在执法人员。"
    elif warning:
        code, label, note = "FIX", "可作出处罚决定，建议补正后归档", "未发现阻断性问题，存在提示项，建议补正材料并留痕。"
    else:
        code, label, note = "PASS", "可作出处罚决定", "四层审查未发现风险项。"

    high_cites = sum(1 for r in results if r["status"] == "fired" for b in r["basis"] if b.get("confidence") == "high")
    medium_cites = sum(1 for r in results if r["status"] == "fired" for b in r["basis"] if b.get("confidence") == "medium")

    result = {
        "case_id": case_id,
        "decision": code,
        "decision_label": label,
        "decision_note": note,
        "behavior": behavior,
        "behavior_label": bh.get("label"),
        "behavior_article": band.get("article") if band else None,
        "fine_min": ctx.get("fine_min"),
        "fine_max": ctx.get("fine_max"),
        "risks": risks,
        "mitigations": mitigations,
        "infos": infos,
        "pending": pending,
        "passed": passed,
        "layers": [
            {
                "id": l["id"],
                "name": l["name"],
                "desc": l["desc"],
                "total": len([r for r in results if r["layer"] == l["id"]]),
                "fired": len([r for r in results if r["layer"] == l["id"] and r["status"] == "fired"]),
                "insufficient": len([r for r in results if r["layer"] == l["id"] and r["status"] == "insufficient"]),
            }
            for l in ruleset()["layers"]
        ],
        "fields": {k: v for k, v in fields.items() if not isinstance(v, (dict,))},
        "conflicts": conflicts,
        "stats": {
            "rules_checked": len(results),
            "fired": len(risks) + len(mitigations) + len(infos),
            "blocking": len(blocking),
            "warning": len(warning),
            "pending": len(pending),
            "needs_human": len([r for r in results if r["status"] == "fired" and r["needs_human"]]),
            "high_cites": high_cites,
            "medium_cites": medium_cites,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        },
        "trail": {
            "ruleset": ruleset()["version"],
            "profile": prof.get("label"),
            "profile_verified": prof.get("verified"),
            "review_date": review_date,
            "engine_mode": "llm+rules" if llm_used else "rules-only",
            "rule_ids": [r["id"] for r in risks + mitigations + infos],
            "presumed": presumed,
            "fingerprint": "%s-%s-%s" % (case_id, ruleset()["version"], int(time.time())),
        },
    }

    if use_llm and llm_used:
        drafted = llm.polish_report(result)
        if drafted:
            result["draft_opinion"] = drafted
    if "draft_opinion" not in result:
        result["draft_opinion"] = _template_opinion(result)
    return result


def _template_opinion(result):
    """无 LLM 时的确定性文书草稿。降级不等于不可用。"""
    parts = ["审查结论：%s。" % result["decision_label"]]
    if result["risks"]:
        tops = result["risks"][:3]
        parts.append("主要问题：" + "；".join("%s（%s）" % (r["title"], r["layer_name"]) for r in tops) + "。")
    if result["mitigations"]:
        parts.append("裁量提示：" + "；".join(r["title"] for r in result["mitigations"]) + "。")
    if result["pending"]:
        parts.append("另有 %d 项因材料信息不足未能完成审查，需补充核实。" % len(result["pending"]))
    parts.append("上述意见由规则引擎基于 %d 条审查要点自动生成，仅供执法人员参考，不替代人工判断。"
                 % result["stats"]["rules_checked"])
    return "".join(parts)
