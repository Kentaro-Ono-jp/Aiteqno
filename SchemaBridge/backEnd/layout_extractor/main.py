import os
import sys

# .pyc を書かせない（既存仕様を踏襲）
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from simple_pipeline import run

def main():
    result = run(
        image_path=None,     # input から自動選択
        save_pdf=True,
        save_png=True,
        save_layout=True,    # layout.json / schema_layout.json を出力
        output_dir=r"C:\work\Aiteqno\output",  # 明示指定（Windows Git Bash/PowerShellどちらでもOK）
        page_size=None
    )

    print(f"🔎 Using image: {result['image']}")
    if result.get("layout_json"):
        print(f"✅ layout.jsonを出力しました → {result['layout_json']}")
    if result.get("schema_layout_json"):
        print(f"✅ schema_layout.jsonを出力しました → {result['schema_layout_json']}")
    if result.get("pdf"):
        print(f"✅ PDFを出力しました → {result['pdf']}")
    if result.get("png"):
        print(f"✅ PNGを出力しました → {result['png']}")

if __name__ == "__main__":
    main()