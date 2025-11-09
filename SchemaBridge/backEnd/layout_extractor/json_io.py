# json_io.py
import os
import json
from typing import Any, Dict, List, Optional

def _normalize_lines(lines: List[Dict[str, int]], pos_tol: int = 2) -> List[Dict[str, int]]:
    """
    Detect and merge duplicated axis-aligned line segments that only differ by a
    few pixels so that layout.json does not double-draw nearly identical lines.
    """

    def _orientation(seg: Dict[str, int]) -> str:
        dx = abs(seg.get("x2", 0) - seg.get("x1", 0))
        dy = abs(seg.get("y2", 0) - seg.get("y1", 0))
        return "h" if dx >= dy else "v"

    def _canonical(seg: Dict[str, int], orient: str) -> Dict[str, int]:
        x1 = int(seg.get("x1", 0))
        y1 = int(seg.get("y1", 0))
        x2 = int(seg.get("x2", 0))
        y2 = int(seg.get("y2", 0))

        if orient == "h":
            if x1 > x2:
                x1, x2 = x2, x1
                y1, y2 = y2, y1
            mid_y = round((y1 + y2) / 2)
            y1 = y2 = mid_y
        else:
            if y1 > y2:
                x1, x2 = x2, x1
                y1, y2 = y2, y1
            mid_x = round((x1 + x2) / 2)
            x1 = x2 = mid_x

        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

    def _is_duplicate(a: Dict[str, int], b: Dict[str, int], orient: str) -> bool:
        if orient == "h":
            if abs(a["y1"] - b["y1"]) > pos_tol:
                return False
            # overlap on x
            return not (a["x2"] < b["x1"] - pos_tol or b["x2"] < a["x1"] - pos_tol)
        else:
            if abs(a["x1"] - b["x1"]) > pos_tol:
                return False
            # overlap on y
            return not (a["y2"] < b["y1"] - pos_tol or b["y2"] < a["y1"] - pos_tol)

    # 1) orient & canonicalize
    prepared: List[Dict[str, int]] = []
    for seg in (lines or []):
        orient = _orientation(seg)
        canon = _canonical(seg, orient)
        canon["orient"] = orient  # annotate
        prepared.append(canon)

    # 2) sweep duplicates and average them
    merged: List[Dict[str, int]] = []
    buckets: List[Dict[str, Any]] = []  # track counts per merged item
    for cand in prepared:
        orient = cand["orient"]
        matched = False
        for idx, entry in enumerate(buckets):
            if entry["orient"] != orient:
                continue
            current = merged[idx]
            if _is_duplicate(current, cand, orient):
                count = entry["count"] + 1
                if orient == "h":
                    current["x1"] = int(round((current["x1"] * entry["count"] + cand["x1"]) / count))
                    current["x2"] = int(round((current["x2"] * entry["count"] + cand["x2"]) / count))
                    current["y1"] = current["y2"] = int(round((current["y1"] * entry["count"] + cand["y1"]) / count))
                else:
                    current["y1"] = int(round((current["y1"] * entry["count"] + cand["y1"]) / count))
                    current["y2"] = int(round((current["y2"] * entry["count"] + cand["y2"]) / count))
                    current["x1"] = current["x2"] = int(round((current["x1"] * entry["count"] + cand["x1"]) / count))
                entry["count"] = count
                matched = True
                break
        if not matched:
            merged.append({"x1": cand["x1"], "y1": cand["y1"], "x2": cand["x2"], "y2": cand["y2"]})
            buckets.append({"orient": orient, "count": 1})

    # 3) strip helper fields and return
    return [{"x1": m["x1"], "y1": m["y1"], "x2": m["x2"], "y2": m["y2"]} for m in merged]

def _resolve_output_dir(output_dir: Optional[str]) -> str:
    """
    出力先ディレクトリを決定して必ず返す（存在しなければ作る）。
    優先度:
      1) 明示指定 output_dir（絶対/相対問わず） -> そのディレクトリを **そのまま** 使う（末尾に 'output' を付けない）
      2) 自動探索:
         - このファイル(__file__)から上へ辿って 'SchemaBridge' を見つけたら、
           その親ディレクトリ直下に 'output' を作る（= Aiteqno/output 想定）
         - 見つからなければ CWD 直下の './output'
    """
    if output_dir:
        # 指定があれば “そのまま” 採用（追加で 'output' は付けない）
        out_dir = output_dir if os.path.isabs(output_dir) else os.path.abspath(output_dir)
    else:
        module_dir = os.path.dirname(os.path.abspath(__file__))
        cur = module_dir
        found = None
        while True:
            head, tail = os.path.split(cur)
            if tail == "SchemaBridge":
                found = head  # SchemaBridge の親 = Aiteqno
                break
            if head == cur:
                break
            cur = head
        if found:
            out_dir = os.path.join(found, "output")
        else:
            out_dir = os.path.abspath(os.path.join(os.getcwd(), "output"))
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def save_layout_json(layout_data: Dict[str, Any], output_dir: Optional[str], filename: str) -> str:
    """
    罫線レイアウトのみのJSONを出力する。
    """
    out_dir = _resolve_output_dir(output_dir)
    path = os.path.join(out_dir, filename)

    raw_lines = layout_data.get("lines") or []
    sanitized = {
        "size": layout_data.get("size"),
        "lines": _normalize_lines(raw_lines),
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(sanitized, f, ensure_ascii=False, indent=2)
    return path


def save_schema_layout_json(layout_data: Dict[str, Any], output_dir: Optional[str], filename: str) -> str:
    """
    入力ボックス等のスキーマ向けレイアウトJSONを出力する。
    """
    out_dir = _resolve_output_dir(output_dir)
    path = os.path.join(out_dir, filename)

    sanitized = {
        "size": layout_data.get("size"),
        "boxes": layout_data.get("boxes") or [],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(sanitized, f, ensure_ascii=False, indent=2)
    return path