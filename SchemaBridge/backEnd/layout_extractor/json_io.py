# json_io.py
import os
import json
from typing import Any, Dict, List, Optional

def _normalize_lines(lines: List[Dict[str, int]], pos_tol: int = 3) -> List[Dict[str, int]]:
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
            mid_y = (y1 + y2) / 2.0
            return {"x1": x1, "y1": mid_y, "x2": x2, "y2": mid_y}

        if y1 > y2:
            x1, x2 = x2, x1
            y1, y2 = y2, y1
        mid_x = (x1 + x2) / 2.0
        return {"x1": mid_x, "y1": y1, "x2": mid_x, "y2": y2}

    def _merge_axis(segments: List[Dict[str, float]], orient: str) -> List[Dict[str, int]]:
        def _coord(seg: Dict[str, float]) -> float:
            return seg["y1"] if orient == "h" else seg["x1"]

        def _start(seg: Dict[str, float]) -> float:
            return seg["x1"] if orient == "h" else seg["y1"]

        def _end(seg: Dict[str, float]) -> float:
            return seg["x2"] if orient == "h" else seg["y2"]

        groups: List[Dict[str, Any]] = []

        for seg in segments:
            coord = _coord(seg)
            start = _start(seg)
            end = _end(seg)
            length = max(abs(end - start), 1.0)

            best_idx: Optional[int] = None
            best_diff: Optional[float] = None
            for idx, group in enumerate(groups):
                diff = abs(group["coord"] - coord)
                if diff <= pos_tol and (best_diff is None or diff < best_diff):
                    best_idx = idx
                    best_diff = diff

            if best_idx is None:
                group = {"coord": coord, "weight": length, "segments": []}
                groups.append(group)
            else:
                group = groups[best_idx]
                total = group["weight"] + length
                group["coord"] = (group["coord"] * group["weight"] + coord * length) / total
                group["weight"] = total

            group["segments"].append({
                "x1": seg["x1"],
                "y1": seg["y1"],
                "x2": seg["x2"],
                "y2": seg["y2"],
                "_coord": coord,
                "_length": length,
            })

        merged_segments: List[Dict[str, int]] = []

        for group in groups:
            ordered = sorted(
                group["segments"],
                key=lambda seg: (_start(seg), _end(seg)),
            )

            current: Optional[Dict[str, float]] = None
            for seg in ordered:
                coord = seg["_coord"]
                start = _start(seg)
                end = _end(seg)
                length = seg["_length"]

                if current is None:
                    current = {
                        "start": start,
                        "end": end,
                        "weight": length,
                        "coord_sum": coord * length,
                    }
                    continue

                if start <= current["end"] + pos_tol:
                    current["coord_sum"] += coord * length
                    current["weight"] += length
                    current["start"] = min(current["start"], start)
                    current["end"] = max(current["end"], end)
                else:
                    merged_segments.append({
                        "orient": orient,
                        "start": current["start"],
                        "end": current["end"],
                        "coord": current["coord_sum"] / current["weight"],
                    })
                    current = {
                        "start": start,
                        "end": end,
                        "weight": length,
                        "coord_sum": coord * length,
                    }

            if current is not None:
                merged_segments.append({
                    "orient": orient,
                    "start": current["start"],
                    "end": current["end"],
                    "coord": current["coord_sum"] / current["weight"],
                })

        cleaned: List[Dict[str, int]] = []
        for seg in merged_segments:
            if seg["orient"] == "h":
                y = int(round(seg["coord"]))
                cleaned.append({
                    "x1": int(round(seg["start"])),
                    "y1": y,
                    "x2": int(round(seg["end"])),
                    "y2": y,
                })
            else:
                x = int(round(seg["coord"]))
                cleaned.append({
                    "x1": x,
                    "y1": int(round(seg["start"])),
                    "x2": x,
                    "y2": int(round(seg["end"])),
                })

        return cleaned

    horizontals: List[Dict[str, float]] = []
    verticals: List[Dict[str, float]] = []

    for seg in lines:
        if not isinstance(seg, dict):
            continue
        orient = _orientation(seg)
        canonical = _canonical(seg, orient)
        if orient == "h":
            horizontals.append(canonical)
        else:
            verticals.append(canonical)

    merged_lines: List[Dict[str, int]] = []
    merged_lines.extend(_merge_axis(horizontals, "h"))
    merged_lines.extend(_merge_axis(verticals, "v"))
    return merged_lines

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