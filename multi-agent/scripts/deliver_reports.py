# -*- coding: utf-8 -*-
"""把报告复制到 D:\\Desktop\\新建文件夹 并逐个校验。"""
import os
import shutil
import sys

SRC = r"C:\Users\17205\Desktop\AgriReports"
WS = r"D:\Desktop\multi-agent-vibecoding-20261114\multi-agent"
DST = r"D:\Desktop\新建文件夹"

MAPPING = [
    (os.path.join(SRC, "agri-report-20261114-154748-3ad8d2.html"), "香蕉冠腐病防治与采后运输要点.html"),
    (os.path.join(SRC, "agri-report-20261114-154751-8b991a.xlsx"), "香蕉冠腐病防治与采后运输要点.xlsx"),
    (os.path.join(SRC, "agri-report-20261114-154756-66f961.pdf"), "香蕉冠腐病防治与采后运输要点.pdf"),
    (os.path.join(WS, "reports", "banana-crown-rot.md"), "香蕉冠腐病防治与采后运输要点.md"),
    (os.path.join(SRC, "agri-report-20261114-154710-45c138.html"), "嗓子疼食疗缓解要点.html"),
    (os.path.join(SRC, "agri-report-20261114-154717-67648b.xlsx"), "嗓子疼食疗缓解要点.xlsx"),
    (os.path.join(SRC, "agri-report-20261114-154736-78087a.pdf"), "嗓子疼食疗缓解要点.pdf"),
    (os.path.join(WS, "reports", "sore-throat-diet.md"), "嗓子疼食疗缓解要点.md"),
]

if not os.path.isdir(DST):
    os.makedirs(DST)
    print("CREATED_DST", DST)

ok = 0
fail = []
for src, name in MAPPING:
    if not os.path.isfile(src):
        fail.append((name, "SOURCE_MISSING", src))
        continue
    dst = os.path.join(DST, name)
    shutil.copy2(src, dst)
    if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
        ok += 1
        print("OK   %-52s %8d bytes <- %s" % (name, os.path.getsize(dst), os.path.basename(src)))
    else:
        fail.append((name, "SIZE_MISMATCH", dst))

print("---")
print("COPIED_OK=%d TOTAL=%d" % (ok, len(MAPPING)))
if fail:
    for f in fail:
        print("FAIL", f)
    sys.exit(1)

print("=== 目标目录清单 ===")
for f in sorted(os.listdir(DST)):
    p = os.path.join(DST, f)
    print("%-56s %8d" % (f, os.path.getsize(p) if os.path.isfile(p) else -1))
