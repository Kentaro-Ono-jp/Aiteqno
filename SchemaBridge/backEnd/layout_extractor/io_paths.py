# io_paths.py
import os
import glob

DEFAULT_IMAGE_NAME = "form_blank_testClinic_v1.png"
DEFAULT_LAYOUT_JSON = os.path.join("output", "layout_a4_portrait.json")
DEFAULT_PDF_OUTPUT = os.path.join("output", "layout_preview.pdf")

def find_repo_root(start_dir: str, anchor="SchemaBridge") -> str:
    cur = start_dir
    while True:
        head, tail = os.path.split(cur)
        if tail == anchor:
            return cur
        if head == cur:
            raise FileNotFoundError(f'"{anchor}" ルートを辿れませんでした。起点: {start_dir}')
        cur = head

def resolve_input_dir(script_dir: str, repo_root: str) -> str:
    # まずは SchemaBridge 配下 → 無ければその親（Aiteqno直下）をフォールバック
    parent_root = os.path.dirname(repo_root)
    candidates = [
        os.path.join(repo_root, "input"),                    # SchemaBridge/input
        os.path.join(parent_root, "input"),                  # Aiteqno/input（今回の配置）
        os.path.join(repo_root, "backEnd", "layout_extractor", "input"),
        os.path.join(repo_root, "layout_extractor", "input"),
        os.path.join(script_dir, "input"),
    ]
    for d in candidates:
        if os.path.isdir(d):
            return d
    tried = "\n  - " + "\n  - ".join(candidates)
    raise FileNotFoundError(f"入力ディレクトリが見つかりません。探索候補:{tried}")

def choose_target_image(input_dir: str, default_name: str = DEFAULT_IMAGE_NAME) -> str:
    target = os.path.join(input_dir, default_name)
    if os.path.isfile(target):
        return target
    pngs = glob.glob(os.path.join(input_dir, "*.[Pp][Nn][Gg]"))
    if not pngs:
        raise FileNotFoundError(f"PNGが見つかりません: {input_dir}（期待ファイル: {default_name}）")
    return max(pngs, key=os.path.getmtime)

def ensure_output_dir(path: str):
    out_dir = os.path.dirname(path) or "."
    os.makedirs(out_dir, exist_ok=True)
    return out_dir