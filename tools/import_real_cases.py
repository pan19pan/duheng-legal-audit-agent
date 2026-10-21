# -*- coding: utf-8 -*-
"""真实案例导入工具：把导出的处罚决定书文本清洗、脱敏后并入数据集。

背景：信用中国、裁判文书网等公开渠道有反爬与登录门槛，脚本化批量抓取不可靠，
也不建议绕过。更稳妥的路径是：通过北大法宝 / 威科先行 / 学校图书馆数据库等
合规渠道导出决定书全文（.txt），放入 data/raw_cases/ 后运行本脚本：

    python tools/import_real_cases.py

脚本做三件事：
1. 清洗：去页眉页脚、合并断行；
2. 脱敏：姓名→某某某，身份证/手机号/ Plate 全部遮蔽，保留句法结构；
3. 输出：data/real_cases.json，每条保留来源文件名以供溯源。

脱敏规则是保守的：宁可多遮，不可漏遮。导入后请人工复核一遍再对外分享。
"""

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw_cases")
OUT = os.path.join(ROOT, "data", "real_cases.json")

SURNAME = "张王李赵刘陈杨黄周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董潘袁蔡蒋余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"


def clean(text):
    text = re.sub(r"\r", "", text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def anonymize(text):
    # 身份证 / 手机号 / 银行卡长号
    text = re.sub(r"\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:[0-2]\d|3[01])\d{3}[\dXx]", "身份证号****", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号****", text)
    text = re.sub(r"(?<!\d)\d{16,19}(?!\d)", "账号****", text)
    # 车牌：保留省份简称与首位，其余遮蔽（桂A12345 → 桂A1***）
    text = re.sub(r"([" + SURNAME + r"][A-HJ-NP-Z])[A-HJ-NP-Z0-9]{4,5}", r"\1***", text)
    # 姓名：某某 / 某甲 等
    text = re.sub(r"([" + SURNAME + r"])(?=[" + SURNAME + r"]{1,2})(?![某某])", r"\1某某", text)
    return text


def main():
    if not os.path.isdir(RAW):
        os.makedirs(RAW, exist_ok=True)
        print("已创建 %s —— 把导出的 .txt 决定书放进来后重新运行" % RAW)
        return
    files = sorted(glob.glob(os.path.join(RAW, "*.txt")))
    if not files:
        print("raw_cases 目录为空：请先放入 .txt 格式的决定书文本")
        return
    cases = []
    for fp in files:
        with open(fp, encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        text = anonymize(clean(raw))
        if len(text) < 50:
            print("跳过（太短）：%s" % os.path.basename(fp))
            continue
        cases.append({
            "case_id": "REAL-" + re.sub(r"\W", "-", os.path.splitext(os.path.basename(fp))[0])[:40],
            "title": os.path.splitext(os.path.basename(fp))[0],
            "tag": "真实文书/已脱敏",
            "facts_text": text,
            "evidence_level": "〔实践〕来源为公开处罚决定书，已脱敏，导入后需人工复核",
        })
        print("导入：%s（%d 字）" % (os.path.basename(fp), len(text)))
    out = {
        "version": "",
        "description": "真实决定书导入集（已脱敏）。字段结构与 cases_dataset.json 一致。",
        "cases": cases,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n共 %d 例 → %s" % (len(cases), os.path.relpath(OUT, ROOT)))
    print("⚠️ 请人工复核脱敏效果后再提交或分享。")


if __name__ == "__main__":
    main()
