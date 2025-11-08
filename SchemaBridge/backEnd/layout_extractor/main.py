import os, sys
# ★ 他のimportより前に設定：これ以降のimportで .pyc を書かせない
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# main.py（薄いエントリポイント）
from reportlab.lib.pagesizes import A4
from pipeline import run_pipeline  # 追加：責務はpipelineへ

def main():
    # 既定：input配下のデフォルト/最新PNGを選び、PDF/PNGを出力
    target_image, pdf_path, png_path = run_pipeline(image_path=None, save_pdf=True, save_png=True, page_size=A4)
    print(f"🔎 Using image: {target_image}")
    if pdf_path:
        print(f"✅ PDFを出力しました → {pdf_path}")
    if png_path:
        print(f"✅ PNGを出力しました → {png_path}")

if __name__ == "__main__":
    main()