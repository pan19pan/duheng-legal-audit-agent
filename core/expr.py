"""三值逻辑表达式求值器。

为什么不用二值布尔？因为法律审查中最危险的不是「判断错」，而是「把没查清的事
当成查清了」。当某个事实字段缺失时，规则既不应判为通过、也不应判为违规，而应
返回 None（未知），由上层把该条规则标为「信息不足，需人工核实」。

这是整个产品在工程上的核心取舍：不确定性必须显性化，不能被 False 吞掉。
"""

UNKNOWN = None


def get_path(ctx, path, default=None):
    if not path:
        return default
    cur = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def referenced_fields(node, acc=None):
    """收集表达式引用到的字段路径，用于判断「这条规则有没有足够信息可审」。"""
    if acc is None:
        acc = []
    if not isinstance(node, dict):
        return acc
    if node.get("op") == "field" and node.get("path"):
        acc.append(node["path"])
    for arg in node.get("args", []) or []:
        referenced_fields(arg, acc)
    return acc


def _cmp(op, a, b):
    if a is None or b is None:
        return UNKNOWN
    try:
        if op == "gt":
            return a > b
        if op == "gte":
            return a >= b
        if op == "lt":
            return a < b
        if op == "lte":
            return a <= b
        if op == "eq":
            return a == b
        if op == "ne":
            return a != b
    except TypeError:
        return UNKNOWN
    return UNKNOWN


def _contains_none(values):
    return any(v is None for v in values)


def resolve(node, ctx):
    if not isinstance(node, dict):
        return node
    op = node.get("op")

    if op == "const":
        return node.get("value")
    if op == "field":
        return get_path(ctx, node.get("path"))
    if op == "always":
        return True

    args = [resolve(a, ctx) for a in (node.get("args") or [])]

    if op == "not":
        v = args[0] if args else UNKNOWN
        return UNKNOWN if v is None else (not v)

    if op == "all":
        if any(v is False for v in args):
            return False
        return UNKNOWN if _contains_none(args) else True

    if op == "any":
        if any(v is True for v in args):
            return True
        return UNKNOWN if _contains_none(args) else False

    if op in ("gt", "gte", "lt", "lte", "eq", "ne"):
        if len(args) != 2:
            return UNKNOWN
        return _cmp(op, args[0], args[1])

    if op == "in":
        subject, pool = (args + [None, None])[:2]
        if subject is None or pool is None:
            return UNKNOWN
        return subject in pool

    if op == "not_in":
        subject, pool = (args + [None, None])[:2]
        if subject is None or pool is None:
            return UNKNOWN
        return subject not in pool

    if op == "empty":
        v = args[0] if args else None
        if v is None:
            return UNKNOWN
        return len(v) == 0

    if op == "nonempty":
        v = args[0] if args else None
        if v is None:
            return UNKNOWN
        return len(v) > 0

    return UNKNOWN
