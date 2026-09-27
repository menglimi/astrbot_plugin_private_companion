# -*- coding: utf-8 -*-
"""单域拆分流水线：切分 -> numstat 守卫 -> 守恒 -> 提交前报告。

用法:
    python tools/run_split_domain.py <methods_file> <new_class> <new_module> "<中文域说明>"

失败即自动 `git checkout -- main.py` 并删除新模块，绝不留下半成品。
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = "C:/Users/XingChina2789/.workbuddy/binaries/python/versions/3.13.12/python.exe"


def sh(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return (proc.stdout or "") + (proc.stderr or "")


def crlf_counts(path: Path) -> tuple[int, int]:
    b = path.read_bytes()
    return b.count(b"\r\n"), b.count(b"\n")


def main() -> int:
    methods_file, new_class, new_module, label = sys.argv[1:5]
    host = ROOT / "main.py"
    target = ROOT / new_module

    before_crlf, before_lf = crlf_counts(host)
    baseline = ROOT / "tmp/night/before_main_methods.txt"
    # 每次拆分前刷新基线：宿主方法集合会随上一轮拆分而变化
    snapshot = ROOT / "tmp/night/snapshot_baseline.py"
    if snapshot.exists():
        sh([PY, str(snapshot)])
    if not baseline.exists():
        raise SystemExit("缺少基线方法清单 tmp/night/before_main_methods.txt")

    print(f"### 拆分 {label} ({new_class} -> {new_module})")
    print(f"宿主拆分前: CRLF={before_crlf} LF={before_lf}")

    out = sh([PY, "tools/split_main_domain.py",
              "--methods-file", methods_file,
              "--new-class", new_class,
              "--new-module", new_module,
              "--domain-label", label,
              "--write"])
    hit = re.search(r"命中方法数\s*:\s*(\d+)", out)
    miss = re.search(r"未命中\s*:\s*(\d+)", out)
    moved = re.search(r"搬走行数\s*:\s*(\d+)", out)
    expect = re.search(r"清单方法数\s*:\s*(\d+)", out)
    if miss and miss.group(1) != "0":
        raise SystemExit(f"未命中非 0，中止:\n{out}")
    if hit and expect and hit.group(1) != expect.group(1):
        raise SystemExit(f"命中数 != 清单数，中止:\n{out}")
    print(f"命中 {hit.group(1) if hit else '?'} / 清单 {expect.group(1) if expect else '?'}，搬走 {moved.group(1) if moved else '?'} 行")

    # --- 守卫 0: 新模块引用的宿主私有件必须先提升到 main_shared ---
    need = sh([PY, "tools/find_promote_candidates.py", new_module])
    candidates = [
        ln.strip() for ln in need.splitlines() if ln.strip() and not ln.startswith("#")
    ]
    if candidates:
        print(f"需提升到 main_shared: {candidates}")
        pr = sh([PY, "tools/promote_to_shared.py", *candidates])
        print(pr.strip())
        if "已提升" not in pr and "无需提升" not in pr:
            print("[FAIL] 提升失败，回滚")
            sh(["git", "checkout", "--", "main.py", "main_shared.py"])
            if target.exists():
                target.unlink()
            return 1

    # --- 守卫 1: CRLF 未被污染 ---
    after_crlf, after_lf = crlf_counts(host)
    guard_fail = False
    if after_crlf < before_crlf * 0.5 and before_crlf > 100:
        print(f"[FAIL] CRLF 崩塌: {before_crlf} -> {after_crlf}（整文件换行被归一化）")
        guard_fail = True
    if after_lf > before_lf - int(moved.group(1)) + 40:
        print(f"[FAIL] LF 总数异常: {before_lf} -> {after_lf}（搬走 {moved.group(1)}）")
        guard_fail = True
    if guard_fail:
        sh(["git", "checkout", "--", "main.py"])
        if target.exists():
            target.unlink()
        print("已回滚 main.py 并删除新模块")
        return 1

    # --- 守卫 2: numstat 行数必须是有限增量 ---
    sh(["git", "add", "-N", new_module])
    ns = sh(["git", "diff", "--numstat", "main.py", new_module]).strip().splitlines()
    print("numstat:")
    for line in ns:
        print(f"  {line}")
        parts = line.split("\t")
        if parts[-1] == "main.py":
            added, deleted = int(parts[0]), int(parts[1])
            if added >= before_lf or deleted >= before_lf:
                print(f"[FAIL] numstat 整文件污染 (added={added} deleted={deleted})")
                sh(["git", "checkout", "--", "main.py"])
                if target.exists():
                    target.unlink()
                return 1

    # --- 守卫 3: 守恒 ---
    cons = sh([PY, "tools/check_main_conservation.py", str(baseline), new_module])
    print(cons.strip())
    if "CONSERVATION: PASS" not in cons:
        sh(["git", "checkout", "--", "main.py"])
        if target.exists():
            target.unlink()
        print("已回滚")
        return 1

    # --- 守卫 4: 语法可解析 ---
    for path in (host, target):
        ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    print("[ok] 宿主与新模块语法解析通过")

    # --- 守卫 5: 新模块可独立导入（缺 import 立刻暴露） ---
    imp = sh([PY, "tools/check_mixin_imports.py", new_module])
    if "IMPORTS: PASS" not in imp:
        print(imp.strip())
        print("[FAIL] 新模块导入失败，回滚")
        sh(["git", "checkout", "--", "main.py"])
        if target.exists():
            target.unlink()
        return 1
    print("[ok] 新模块 AST 级依赖检查通过")

    print(f"main.py 现为 {len(host.read_text(encoding='utf-8').splitlines())} 行；{new_module} 为 {len(target.read_text(encoding='utf-8').splitlines())} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
