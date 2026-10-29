#!/usr/bin/env python3
"""从 papers 文件夹读取 PDF 论文，提取标题并自动生成关键词"""

import glob
import os
import sys

from pypdf import PdfReader

from manage_papers import extract_keywords_from_title
from paper_config import load_config, save_config
from settings import DATA_ROOT

PAPERS_DIR = DATA_ROOT / "papers"


def extract_title_from_pdf(pdf_path: str) -> str:
    """Extract title from PDF file."""
    try:
        reader = PdfReader(pdf_path)
        if len(reader.pages) == 0:
            return ""

        first_page = reader.pages[0]
        text = first_page.extract_text() or ""

        lines = [line.strip() for line in text.split("\n") if line.strip()]

        if lines:
            title = lines[0]
            if len(title) > 200:
                title = title[:200]
            return title

        return os.path.basename(pdf_path).replace(".pdf", "")
    except Exception as e:
        print(f"⚠️ 无法读取 PDF: {pdf_path} - {e}")
        return os.path.basename(pdf_path).replace(".pdf", "")


def process_papers_folder():
    """Process all PDF files in the papers folder."""
    if not os.path.exists(PAPERS_DIR):
        print(f"❌ papers 文件夹不存在: {PAPERS_DIR}")
        return

    pdf_files = glob.glob(os.path.join(PAPERS_DIR, "*.pdf"))

    if not pdf_files:
        print("⚠️ papers 文件夹中没有 PDF 文件")
        return

    print(f"📚 找到 {len(pdf_files)} 个 PDF 文件\n")

    config = load_config()
    existing_titles = {p["title"].lower() for p in config["papers"]}

    new_papers = []

    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        print(f"📄 处理: {filename}")

        title = extract_title_from_pdf(pdf_path)
        print(f"   标题: {title}")

        if title.lower() in existing_titles:
            print("   ⚠️ 论文已存在，跳过")
            continue

        print("   🤖 正在提取关键词...")
        keywords = extract_keywords_from_title(title)
        print(f"   ✅ 关键词: {keywords}")

        paper = {"title": title, "keywords": keywords, "pdf_path": filename}
        new_papers.append(paper)
        existing_titles.add(title.lower())
        print()

    if new_papers:
        config["papers"].extend(new_papers)
        save_config(config)
        print(f"✅ 已添加 {len(new_papers)} 篇论文到配置")
    else:
        print("ℹ️ 没有新论文需要添加")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        pdf_files = glob.glob(os.path.join(PAPERS_DIR, "*.pdf"))
        if pdf_files:
            print("\n📂 papers 文件夹中的 PDF 文件:")
            for i, pdf_path in enumerate(pdf_files, 1):
                print(f"  {i}. {os.path.basename(pdf_path)}")
        else:
            print("⚠️ papers 文件夹中没有 PDF 文件")
    else:
        process_papers_folder()


if __name__ == "__main__":
    main()
