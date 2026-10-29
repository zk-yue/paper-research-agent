#!/usr/bin/env python3
"""管理感兴趣的论文和关键词配置"""

import json
import sys

from paper_config import empty_config, load_config, save_config


def extract_keywords_from_title(title: str) -> list:
    """Use LLM to extract keywords from paper title."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from llm import get_llm

    system_prompt = """你是一位具身智能和机器人领域的资深研究员。
请从给定的论文标题中提取 5-8 个相关的搜索关键词或短语。
这些关键词应该：
1. 涵盖论文的核心技术和方法
2. 包含该领域的通用术语
3. 适合用于 arXiv 论文检索

请直接输出一个 JSON 数组，不要输出其他内容。例如：
["robot manipulation", "vision language action model", "long horizon task"]"""

    user_prompt = f"论文标题：{title}"

    llm = get_llm()
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )

    try:
        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        keywords = json.loads(content)
        if isinstance(keywords, list):
            return keywords
    except (json.JSONDecodeError, IndexError):
        pass

    return []


def add_paper(title, keywords=None):
    """Add a paper and its keywords to the configuration."""
    config = load_config()

    if not keywords:
        print("🤖 正在从论文标题自动提取关键词...")
        keywords = extract_keywords_from_title(title)
        if keywords:
            print(f"✅ 自动提取了 {len(keywords)} 个关键词: {keywords}")
        else:
            print("⚠️ 无法自动提取关键词，请手动指定")

    paper = {"title": title, "keywords": keywords or []}

    if not any(p["title"].lower() == title.lower() for p in config["papers"]):
        config["papers"].append(paper)
        save_config(config)
        print(f"✅ 已添加论文: {title}")
    else:
        print(f"⚠️ 论文已存在: {title}")


def add_keywords(keywords):
    """Add keywords to the configuration."""
    config = load_config()

    new_keywords = [k.strip() for k in keywords if k.strip() and k not in config["keywords"]]
    config["keywords"].extend(new_keywords)
    save_config(config)
    print(f"✅ 已添加 {len(new_keywords)} 个关键词: {new_keywords}")


def list_config():
    """List all papers and keywords in the configuration."""
    config = load_config()

    print("\n📚 已保存的论文:")
    if config["papers"]:
        for i, paper in enumerate(config["papers"], 1):
            keywords = ", ".join(paper.get("keywords", []))
            print(f"  {i}. {paper['title']}")
            if keywords:
                print(f"     关键词: {keywords}")
    else:
        print("  (暂无)")

    print("\n🏷️ 已保存的关键词:")
    if config["keywords"]:
        print(f"  {', '.join(config['keywords'])}")
    else:
        print("  (暂无)")


def clear_config():
    """Clear all papers and keywords."""
    save_config(empty_config())
    print("✅ 已清空所有配置")


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python manage_papers.py list              # 查看所有配置")
        print("  python manage_papers.py add-paper <标题>  # 添加论文（自动提取关键词）")
        print(
            "  python manage_papers.py add-paper <标题> <关键词1,关键词2,...>  # 添加论文（手动指定关键词）"
        )
        print("  python manage_papers.py add-keywords <关键词1,关键词2,...>      # 添加关键词")
        print("  python manage_papers.py clear              # 清空所有配置")
        return

    command = sys.argv[1]

    if command == "list":
        list_config()
    elif command == "add-paper":
        if len(sys.argv) < 3:
            print("❌ 请提供论文标题")
            return
        title = sys.argv[2]
        keywords = sys.argv[3].split(",") if len(sys.argv) > 3 else None
        add_paper(title, keywords)
    elif command == "add-keywords":
        if len(sys.argv) < 3:
            print("❌ 请提供关键词")
            return
        keywords = sys.argv[2].split(",")
        add_keywords(keywords)
    elif command == "clear":
        clear_config()
    else:
        print(f"❌ 未知命令: {command}")


if __name__ == "__main__":
    main()
