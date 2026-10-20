"""可选的 LLM 增强层（OpenAI 兼容接口，零依赖实现）。

产品定位上，LLM 只做两件事：
  1) 读文书 —— 把自然语言案件描述抽成结构化要素；
  2) 写文书 —— 把结构化的审查结论组织成执法人员读得懂的审查意见。
判定权不下放给模型。这样即使模型产生幻觉，也不会直接变成处罚结论。

未配置密钥时不报错，自动降级为纯规则模式（engine_mode = rules-only），
保证任何人 clone 下来都能一键跑通——这是作品集的可用性底线。
"""

import json
import os
import re
import urllib.error
import urllib.request

FACT_SCHEMA_HINT = """你是交通行政执法案件的事实抽取器。请从案件描述中抽取以下字段，
只输出 JSON，不要解释。无法确证的字段一律填 null，禁止猜测。

字段说明：
- violation_time: 违法行为发生时间，格式 YYYY-MM-DD HH:MM
- location: 违法地点（道路/路段名称）
- plate: 车牌号
- speed_limit: 限速值（数字，km/h）
- measured_speed: 实测速度（数字，km/h）
- proposed_fine: 拟处罚款金额（数字，元）
- penalty_type: 处罚种类，如"罚款""警告""吊销机动车驾驶证"
- harm_level: "none" | "minor" | "serious"
- 布尔字段（true/false/null）：device_certified, device_published, device_calibrated,
  evidence_legible, driver_identified, rights_notified, hearing_right_notified,
  legal_review_done, agency_has_jurisdiction, prior_fine_same_act, duplicate_record,
  first_offense, corrected, minor_violation, subjective_fault_excused, correction_order_issued
- mitigating_factors / aggravating_factors: 字符串数组或 null
"""


def config():
    base = os.environ.get("DUHENG_LLM_BASE", "").rstrip("/")
    key = os.environ.get("DUHENG_LLM_KEY", "")
    model = os.environ.get("DUHENG_LLM_MODEL", "gpt-4o-mini")
    if not base or not key:
        return None
    return {"base": base, "key": key, "model": model}


def available():
    return config() is not None


def _chat(messages, temperature=0.0, timeout=30):
    cfg = config()
    if not cfg:
        return None
    payload = json.dumps({
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg["key"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError, OSError):
        return None


def _parse_json(text):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None


def extract_facts(text):
    """返回 (字段字典, 原始输出)；失败返回 (None, None)。"""
    content = _chat([
        {"role": "system", "content": FACT_SCHEMA_HINT},
        {"role": "user", "content": text},
    ])
    data = _parse_json(content)
    if not isinstance(data, dict):
        return None, content
    clean = {}
    for k, v in data.items():
        if v is None or v == "":
            continue
        if isinstance(v, list) and not v:
            continue
        clean[k] = v
    return clean, content


REPORT_HINT = """你是行政执法案件的文书助手。下面给出一次结构化审查的结果（JSON）。
请写一段 120–180 字的中文审查意见，要求：
1) 先给结论；2) 按「程序—事实—法律适用—裁量」顺序说明主要问题；
3) 只使用 JSON 中已有的事实，不得添加任何新的案件事实或法条；
4) 不使用感叹号，不用"我们认为"这类模糊表述。"""


def polish_report(result):
    payload = {
        "decision": result.get("decision"),
        "decision_label": result.get("decision_label"),
        "risks": [{"title": r["title"], "finding": r["finding"]} for r in result.get("risks", [])],
        "mitigations": [{"title": r["title"], "finding": r["finding"]} for r in result.get("mitigations", [])],
    }
    content = _chat([
        {"role": "system", "content": REPORT_HINT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ], temperature=0.2)
    return content.strip() if content else None
