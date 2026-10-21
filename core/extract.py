"""事实要素抽取：把执法文书的自然语言描述转成结构化字段。

设计取向：
1. 先跑规则抽取（快、免费、可预测、可离线），LLM 仅在配置了 API 时作为增强层叠加。
2. 只抽「有明确语言线索」的字段。抽不到就留空（None），交给引擎标为待核实，
   而不是给一个看起来合理的默认值——错误默认值比空值危险得多。
"""

import re
from datetime import datetime

BEHAVIOR_KEYWORDS = [
    ("speeding", ["超速", "超过规定时速", "限速", "时速", "行驶速度"]),
    ("red_light", ["闯红灯", "信号灯", "红灯"]),
    ("no_seatbelt", ["安全带"]),
    ("phone_while_driving", ["拨打", "接听", "手持电话", "玩手机"]),
    ("illegal_parking", ["违停", "违法停放", "停放", "停车"]),
]

PLATE_RE = re.compile(r"([京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼])[A-Z][A-Z0-9]{4,6}")


def _first(pattern, text, group=1):
    m = re.search(pattern, text)
    return m.group(group) if m else None


def parse_datetime(text):
    """从文本中提取第一个日期时间，返回 ISO 字符串。"""
    if not text:
        return None
    patterns = [
        (r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(?:(\d{1,2})[时点:](\d{1,2})?分?)?", True),
        (r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", False),
    ]
    for pat, _ in patterns:
        m = re.search(pat, text)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh = int(m.group(4)) if m.group(4) else 0
            mm = int(m.group(5)) if m.group(5) else 0
            try:
                return datetime(y, mo, d, hh, mm).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                return None
    return None


def classify_behavior(text):
    for key, words in BEHAVIOR_KEYWORDS:
        if any(w in text for w in words):
            return key
    return "unknown"


def _time_near(text, keywords):
    """在同一分句内查找与关键词共现的时间，避免把「案发时间」误当成「录入时间」。"""
    for clause in re.split(r"[。；，,\n]", text or ""):
        if any(k in clause for k in keywords):
            dt = parse_datetime(clause)
            if dt:
                return dt
    return None


def _num(pattern, text):
    v = _first(pattern, text)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def extract_from_text(text):
    """返回从自然语言中可确证的字段；无法确证的键不会出现在结果里。"""
    t = text or ""
    out = {}

    dt = parse_datetime(t)
    if dt:
        out["violation_time"] = dt

    collect = _time_near(t, ["采集", "抓拍"])
    if collect:
        out["collect_time"] = collect
    entry = _time_near(t, ["录入"])
    if entry:
        out["entry_time"] = entry
    notice = _time_near(t, ["告知", "送达"])
    if notice:
        out["notice_time"] = notice

    loc = _first(r"在([A-Za-z0-9\u4e00-\u9fa5]{2,15}?(?:路|街|大道|路口|交叉口|高速|隧道|大桥|桥))", t)
    if loc:
        out["location"] = loc

    plate = PLATE_RE.search(t)
    if plate:
        out["plate"] = plate.group(0)

    # 限速 / 实测速度：分开匹配，避免「限速 60，实测 78」串位
    limit = None
    for pat in [r"限速(?:值|标志)?(?:为|是|：|:)?\s*(\d{2,3})\s*(?:km|公里)?",
                r"最高时速\s*(\d{2,3})",
                r"规定时速\s*(\d{2,3})"]:
        limit = _num(pat, t)
        if limit is not None:
            break
    if limit is not None:
        out["speed_limit"] = limit

    measured = None
    for pat in [r"实测(?:速度|时速)?(?:为|是|：|:)?\s*(\d{2,3})",
                r"行驶速度(?:为|是|：|:)?\s*(\d{2,3})",
                r"车\s*速(?:为|是|：|:)?\s*(\d{2,3})",
                r"时速\s*(\d{2,3})\s*(?:km|公里)"]:
        measured = _num(pat, t)
        if measured is not None:
            break
    if measured is not None:
        out["measured_speed"] = measured

    fine = None
    for pat in [r"罚款\s*(\d+(?:\.\d+)?)\s*元",
                r"处罚款\s*(\d+(?:\.\d+)?)\s*元",
                r"拟(?:处|罚)\s*(\d+(?:\.\d+)?)\s*元",
                r"处\s*(\d+(?:\.\d+)?)\s*元罚款"]:
        fine = _num(pat, t)
        if fine is not None:
            break
    if fine is not None:
        out["proposed_fine"] = fine

    if re.search(r"吊销", t):
        out["penalty_type"] = "吊销机动车驾驶证"

    # —— 程序与证据类布尔字段：先匹配否定表述，再匹配肯定表述 ——
    def flag(key, neg, pos):
        if neg and re.search(neg, t):
            out[key] = False
        elif pos and re.search(pos, t):
            out[key] = True

    flag("device_certified", r"设备未经(?:法制|技术)?审核|未经法制和技术审核",
         r"设备(?:已)?经[^。；]{0,10}审核|已通过[^。；]{0,10}审核")
    flag("device_published", r"地点未(?:向)?(?:社会)?公布|未公布设置地点",
         r"(?:设置)?地点已(?:向社会)?公布|已向社会公布")
    flag("device_calibrated", r"未检定|超期未检|检定(?:已)?过期|不在检定有效期",
         r"检定(?:有效|合格)|在检定有效期内")
    flag("evidence_legible", r"模糊|无法辨认|不可辨认|不清晰|无法看清", r"清晰可辨|清晰|可辨认")
    flag("driver_identified", r"驾驶人不在现场|无法(?:识别|确认)驾驶人|未能识别驾驶人|尚未确认驾驶人",
         r"驾驶人(?:系|为)|经查驾驶人|已确认驾驶人")
    flag("rights_notified", r"未(?:告知|载明)(?:其)?(?:依法享有的)?(?:陈述|申辩)", r"已(?:告知|载明)(?:陈述|申辩)")
    flag("hearing_right_notified", r"未告知听证|未告知(?:其)?要求听证", r"已告知听证")
    flag("legal_review_done", r"未(?:经)?法制审核|未见法制审核", r"已完成法制审核|经法制审核")
    flag("agency_has_jurisdiction", r"无管辖权|非本辖区|不属本机关管辖", r"有管辖权|属本机关管辖")
    flag("prior_fine_same_act", None, r"此前已(?:被)?(?:处以)?(?:处罚|罚款)|已被处以罚款|已作出.{0,6}处罚决定")
    # 违停前置条件：肯定表述优先（"在现场但拒绝驶离"同时命中两类线索，须按"拒绝驶离"处理）
    if re.search(r"驾驶人不在现场|拒绝(?:立即)?驶离", t):
        out["driver_absent_or_refused"] = True
    elif re.search(r"驾驶人(?:仍)?在现场", t):
        out["driver_absent_or_refused"] = False
    flag("duplicate_record", r"未发现重复|查重未发现|无重复记录", r"重复(?:录入|记录)|同一行为.{0,8}(?:多条|两条|三次|多次)记录|记录两次|分别录入")
    flag("owner_notified", r"未向.{0,8}(?:所有人|车主).{0,4}告知|尚未向", r"向(?:机动车)?(?:所有人|车主).{0,4}(?:告知|送达)")
    flag("first_offense", r"非首次|多次违法|此前有违法记录", r"首次违法|初次违法|第一次违法|无违法记录")
    flag("corrected", r"未改正|拒不改正|未驶离", r"已(?:及时)?改正|已整改|已驶离|当场改正")
    flag("minor_violation", None, r"情节轻微|违法行为轻微|轻微违法")
    flag("subjective_fault_excused", None, r"无主观过错|没有主观过错|已尽到.{0,6}注意义务")
    flag("correction_order_issued", r"未(?:下达|作出)责令改正", r"已(?:下达|作出)责令改正")
    flag("law_version_current", r"已废止|旧版|修订前条款|引用(?:的是)?失效", None)

    # 危害后果：三级
    if re.search(r"未造成|没有造成|无危害后果|未造成人员伤亡|无人员伤亡", t):
        out["harm_level"] = "none"
    elif re.search(r"轻微|轻微后果|短暂拥堵", t):
        out["harm_level"] = "minor"
    elif re.search(r"致人(?:受伤|死亡)|造成事故|严重拥堵|重大", t):
        out["harm_level"] = "serious"

    # 从轻 / 从重情节
    mitigating = [w for w in ["主动消除危害后果", "主动减轻危害后果", "主动配合调查", "主动供述", "受他人胁迫", "受他人诱骗"] if w in t]
    if mitigating:
        out["mitigating_factors"] = mitigating
    aggravating = [w for w in ["造成交通事故", "多次违法", "拒不改正", "逃避检查", "伪造证据"] if w in t]
    if aggravating:
        out["aggravating_factors"] = aggravating

    return out


def merge(structured, extracted):
    """结构化补录优先，文本抽取补齐空缺。返回 (字段, 冲突列表)。"""
    merged = dict(extracted or {})
    conflicts = []
    for k, v in (structured or {}).items():
        if v is None or v == "":
            continue
        if k in merged and merged[k] != v:
            conflicts.append({"field": k, "from_text": merged[k], "from_form": v})
        merged[k] = v
    return merged, conflicts
