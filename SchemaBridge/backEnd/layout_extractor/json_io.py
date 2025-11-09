# json_io.py
import os
import json
from typing import Any, Dict, Optional

import os
import json
from typing import Any, Dict, Optional

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

    sanitized = {
        "size": layout_data.get("size"),
        "lines": layout_data.get("lines") or [],
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