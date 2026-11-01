"""从独立预览资源生成参数附录；不读取或写回正式前端。"""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parent
RES = ROOT / "预览资源"
OUT = ROOT / "参数附录"


def css_entries(path):
    raw = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), raw, flags=re.S)
    stack, start, entries = [], 0, []
    for match in re.finditer(r"[{};]", text):
        part = text[start:match.start()].strip()
        char = match.group()
        if char == "{":
            stack.append(part)
        elif char == "}":
            if stack:
                stack.pop()
        elif stack and ":" in part:
            name, value = part.split(":", 1)
            if re.fullmatch(r"[-\w]+", name.strip()):
                offset = start + len(text[start:match.start()]) - len(text[start:match.start()].lstrip())
                entries.append({"name": name.strip(), "value": value.strip(), "selector": " → ".join(stack), "file": path.relative_to(ROOT).as_posix(), "line": raw.count("\n", 0, offset) + 1})
        start = match.end()
    return entries


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("`", "'")


def table(title, rows):
    content = [f"# {title}", "", "由 `更新参数索引.py` 从独立资源生成。数值是现状记录，不是每项都允许自由修改；语义与约束见《参数与动效》。", "", "| 名称／属性 | 值 | 条件／选择器 | 来源 |", "|---|---|---|---|"]
    content.extend(f"| `{cell(r['name'])}` | `{cell(r['value'])}` | `{cell(r['selector'])}` | {r['file']}:{r['line']} |" for r in rows)
    return "\n".join(content) + "\n"


def main():
    OUT.mkdir(exist_ok=True)
    all_entries = []
    for path in sorted((RES / "css").glob("*.css")):
        entries = css_entries(path)
        all_entries.extend(entries)
        normal = [r for r in entries if not r["name"].startswith("--")]
        (OUT / f"组件样式-{path.stem}.md").write_text(table(f"组件样式：{path.name}", normal), encoding="utf-8")
    variables = [r for r in all_entries if r["name"].startswith("--")]
    (OUT / "CSS变量.md").write_text(table("CSS 变量与主题覆盖值", variables), encoding="utf-8")
    patterns, motions, icons = [], [], []
    for path in sorted((RES / "css").glob("*.css")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"@(media|supports|keyframes|property)", line):
                patterns.append({"name": "CSS 条件／关键帧", "value": line.strip(), "selector": path.stem, "file": path.relative_to(ROOT).as_posix(), "line": i})
    for path in sorted((RES / "js").glob("*.js")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"duration:|delay:|stiffness:|damping:|setTimeout\(|MAX_FILES|MiB|Math\.min\(200|rRatio|holdDampen|longPress|\b(?:const|let)\b.*\d|^\s*\w+:\s*[-.\d]", line):
                motions.append({"name": "动效／局部常量", "value": line.strip(), "selector": path.stem, "file": path.relative_to(ROOT).as_posix(), "line": i})
    html = (ROOT / "页面模板.html").read_text(encoding="utf-8")
    for m in re.finditer(r'<symbol id="([^"]+)"[^>]*>', html):
        icons.append(m.group(1))
    geometry = [{"name": "HTML／SVG 几何与内联值", "value": line.strip(), "selector": "页面外壳与中枢", "file": "页面模板.html", "line": i} for i, line in enumerate(html.splitlines(), 1) if re.search(r'\b(?:style|viewBox|points|width|height|rx|ry|cx|cy|r|d)="', line)]
    (OUT / "断点与关键帧.md").write_text(table("媒体断点、特性分支与关键帧位置", patterns), encoding="utf-8")
    (OUT / "JS动效与局部参数.md").write_text(table("JavaScript 动效与局部参数位置", motions), encoding="utf-8")
    (OUT / "HTML与SVG几何.md").write_text(table("HTML 内联参数与 SVG 几何", geometry), encoding="utf-8")
    (OUT / "SVG图标.md").write_text("# SVG 图标目录\n\n使用 `N.icon(name)` 或 `<use href=\"#i-name\">`；图标几何见同页 symbol。\n\n" + "\n".join(f"- `{name}`" for name in icons) + "\n", encoding="utf-8")
    (OUT / "参数索引.json").write_text(json.dumps({"css": all_entries, "conditions": patterns, "motion": motions, "icons": icons}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {len(variables)} 条变量声明、{len(all_entries)-len(variables)} 条组件声明、{len(patterns)} 个条件／关键帧入口、{len(motions)} 个 JS 参数入口、{len(icons)} 个图标。")


if __name__ == "__main__":
    main()
