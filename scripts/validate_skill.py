#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_skill.py —— 按 Agent Skills 规范校验本仓库的 SKILL.md

校验项（对应 https://agentskills.io/specification ）:
  - SKILL.md 存在, 且首行是 --- (frontmatter 必须从第一行开始)
  - name: 必填, 1-64 字符, 仅 a-z0-9-, 不以 - 开头/结尾, 无连续 --, 且等于所在目录名
  - description: 必填, 1-1024 字符, 含 "何时使用" 的语义提示
  - license: 可选, 短字符串
  - compatibility: 可选, 1-500 字符
  - metadata: 可选, 字符串到字符串的映射
  - 正文非空, 且行数不超过 500 行(超过应考虑拆到 references/)

用法:
    python3 scripts/validate_skill.py [技能目录]

退出码: 0 通过, 1 有错误, 2 用法错误
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_BODY_LINES = 500


def parse_frontmatter(text: str) -> tuple[dict[str, object], str, str]:
    """极简 YAML 解析: 只支持顶层 key: value 与一层缩进映射。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "", "frontmatter 必须从文件第一行开始 (首行应为 ---)"
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, "", "frontmatter 未闭合 (缺少结束的 ---)"

    data: dict[str, object] = {}
    current_map: dict[str, str] | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[:1] in (" ", "\t") and current_map is not None:
            k, _, v = raw.strip().partition(":")
            current_map[k.strip()] = v.strip().strip("'\"")
            continue
        key, _, value = raw.partition(":")
        key, value = key.strip(), value.strip()
        if not value:
            current_map = {}
            data[key] = current_map
        else:
            current_map = None
            data[key] = value.strip("'\"")
    body = "\n".join(lines[end + 1 :])
    return data, body, ""


def check(skill_dir: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"缺少 SKILL.md: {skill_md}"], warnings

    text = skill_md.read_text(encoding="utf-8")
    meta, body, err = parse_frontmatter(text)
    if err:
        return [err], warnings

    if any(line.lstrip().startswith("<") for line in text.splitlines()[:12]):
        errors.append("frontmatter 中不要使用尖括号 <>（会被渲染成 &lt; &gt;）")

    name = str(meta.get("name", ""))
    if not name:
        errors.append("缺少必填字段 name")
    else:
        if len(name) > 64:
            errors.append(f"name 超过 64 字符（当前 {len(name)}）")
        if not NAME_RE.match(name):
            errors.append("name 只能包含小写字母、数字和连字符，且不能以连字符开头/结尾或出现连续 --")
        if name != skill_dir.name:
            warnings.append(
                f"name('{name}') 与目录名('{skill_dir.name}') 不一致；安装到技能目录时请让目录名等于 name，"
                f"例如 git clone <repo> {name}，或 ln -s \"$PWD\" ~/.claude/skills/{name}"
            )

    desc = str(meta.get("description", ""))
    if not desc:
        errors.append("缺少必填字段 description")
    else:
        if len(desc) > 1024:
            errors.append(f"description 超过 1024 字符（当前 {len(desc)}）")
        if not re.search(r"(when|use when|适用于|用于|当.*时|使用)", desc, re.IGNORECASE):
            errors.append("description 建议同时说明「做什么」与「何时使用」（缺少 when/适用于 之类的措辞）")

    lic = meta.get("license")
    if lic is not None and not isinstance(lic, str):
        errors.append("license 必须是短字符串（如 MIT）")

    compat = meta.get("compatibility")
    if isinstance(compat, str):
        if len(compat) > 500:
            errors.append(f"compatibility 超过 500 字符（当前 {len(compat)}）")
    elif compat is not None:
        errors.append("compatibility 必须是字符串")

    md = meta.get("metadata")
    if md is not None and not isinstance(md, dict):
        errors.append("metadata 必须是字符串键值映射")

    if not body.strip():
        errors.append("正文为空：frontmatter 之后需要有技能说明")
    body_lines = len([l for l in body.splitlines() if l.strip()])
    if body_lines > MAX_BODY_LINES:
        errors.append(f"正文 {body_lines} 行，超过建议上限 {MAX_BODY_LINES} 行，请拆到 references/")

    for ref in re.findall(r"\]\(([^)#]+\.md)\)", body):
        if ref.startswith(("http://", "https://")):
            continue
        if not (skill_dir / ref).is_file():
            errors.append(f"正文引用的文件不存在: {ref}")

    return errors, warnings


def main(argv: list[str]) -> int:
    skill_dir = Path(argv[1] if len(argv) > 1 else ".").resolve()
    if not skill_dir.is_dir():
        print(f"[!] 不是目录: {skill_dir}", file=sys.stderr)
        return 2

    errors, warnings = check(skill_dir)
    for w in warnings:
        print(f"[!] 提示: {w}")
    if errors:
        print(f"[x] {skill_dir.name}: {len(errors)} 项不符合 Agent Skills 规范")
        for e in errors:
            print(f"    - {e}")
        return 1

    suffix = f"（{len(warnings)} 项提示）" if warnings else ""
    print(f"[ok] {skill_dir.name}: SKILL.md 符合 Agent Skills 规范{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
