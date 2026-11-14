# -*- coding: utf-8 -*-
"""交付物抽检：xlsx 结构、html 引用、pdf 头与页数。"""
import os
import zipfile

import openpyxl

D = r"D:\Desktop\新建文件夹"

for n in ["香蕉冠腐病防治与采后运输要点.xlsx", "嗓子疼食疗缓解要点.xlsx"]:
    p = os.path.join(D, n)
    wb = openpyxl.load_workbook(p)
    ws = wb.active
    print("[XLSX]", n, "| sheets=", wb.sheetnames, "| dims=", ws.dimensions)
    for row in ws.iter_rows(min_row=1, max_row=3, values_only=True):
        print("    ", [str(c)[:36] if c is not None else "" for c in row])

for n in ["香蕉冠腐病防治与采后运输要点.html", "嗓子疼食疗缓解要点.html"]:
    t = open(os.path.join(D, n), encoding="utf-8", errors="ignore").read()
    print("[HTML]", n, "| chars=", len(t), "| has_K=", "K1" in t, "| has_doc=", ("thlz" in t or "xh8Y" in t))

for n in ["香蕉冠腐病防治与采后运输要点.pdf", "嗓子疼食疗缓解要点.pdf"]:
    p = os.path.join(D, n)
    b = open(p, "rb").read()
    print("[PDF ]", n, "| magic=", b[:5], "| pages=", b.count(b"/Type /Page") + b.count(b"/Type/Page"), "| bytes=", len(b))
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            PdfReader = None
    if PdfReader:
        r = PdfReader(p)
        txt = (r.pages[0].extract_text() or "").replace("\n", " ")[:120]
        print("      reader_pages=", len(r.pages), "| page1=", txt)

for n in ["香蕉冠腐病防治与采后运输要点.md", "嗓子疼食疗缓解要点.md"]:
    t = open(os.path.join(D, n), encoding="utf-8").read()
    print("[MD  ]", n, "| chars=", len(t), "| headings=", t.count("\n#"))
