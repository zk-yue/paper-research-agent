#!/usr/bin/env python3
"""Rich terminal interface for the paper research workflow."""

import argparse

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from graph import build_graph
from history import format_history_list_for_display, load_history_list, save_history
from tools import format_papers_for_display

console = Console()


def run_cli(year_from: int, keywords: list[str]):
    """Run the research workflow with Rich CLI interface."""
    console.print()
    console.print(
        Panel(
            f"[bold cyan]🔬 Paper Research Agent[/bold cyan]\n"
            f"[dim]关键词: {', '.join(keywords)}[/dim]",
            title="文献调研系统",
            border_style="cyan",
        )
    )
    console.print()

    # 创建进度条
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
        transient=False,
    ) as progress:
        # 创建任务
        task_init = progress.add_task("[cyan]初始化工作流...", total=100)
        task_literature = progress.add_task("[yellow]文献调研 Agent...", total=100, visible=False)
        task_history = progress.add_task("[white]保存历史记录...", total=100, visible=False)

        # 初始化
        progress.update(task_init, advance=50)
        graph = build_graph()
        progress.update(task_init, advance=50)

        state = {
            "keywords": keywords,
            "year_from": int(year_from),
        }

        literature_review = ""
        papers_display = ""
        execution_log = ""

        history_id = None

        # 执行工作流
        for event in graph.stream(state, stream_mode="updates"):
            for node, update in event.items():
                if "literature_review" in update:
                    progress.update(task_literature, visible=True, advance=100)
                    literature_review = update["literature_review"]
                    papers = update.get("papers", [])
                    representative = update.get("representative_papers", [])
                    papers_display = format_papers_for_display(papers, representative)
                    log = update.get("execution_log", [])
                    execution_log = "\n".join(log)
                    console.print()
                    if literature_review:
                        console.print(
                            Panel(
                                Markdown(literature_review[:500] + "..."),
                                title="📚 文献调研框架（预览）",
                                border_style="yellow",
                            )
                        )
                    else:
                        console.print(
                            "暂无已确认直接相关论文，已跳过文献调研与综述生成；可在历史记录中查看筛选详情。"
                        )

                    progress.update(task_history, visible=True, advance=50)
                    history_id = save_history(
                        keywords=keywords,
                        year_from=int(year_from),
                        literature_review=literature_review,
                        papers_display=papers_display,
                        execution_log=execution_log,
                        research_details={
                            "papers": papers,
                            "screening": update.get("screening", {}),
                            "search_diagnostics": update.get("search_diagnostics", []),
                            "search_warnings": update.get("search_warnings", []),
                        },
                    )
                    progress.update(task_history, advance=50)

        console.print()
        console.print(Rule("[bold green]✅ 研究流程完成[/bold green]"))
        console.print()

        # 显示结果摘要
        summary_table = Table(title="📊 结果摘要")
        summary_table.add_column("模块", style="cyan")
        summary_table.add_column("状态", style="green")
        summary_table.add_column("输出长度", style="yellow")

        summary_table.add_row(
            "文献调研",
            "✅ 完成" if literature_review else "已跳过",
            f"{len(literature_review)} 字符",
        )

        console.print(summary_table)
        console.print()

        # 显示历史记录
        history_list = load_history_list()
        if history_list:
            console.print(
                Panel(
                    format_history_list_for_display(history_list),
                    title="📜 最近历史记录",
                    border_style="blue",
                )
            )
            console.print()

        if history_id:
            console.print(f"[bold]🆔 历史记录 ID:[/bold] [cyan]{history_id}[/cyan]")
        console.print()


def main():
    parser = argparse.ArgumentParser(description="Paper Research Agent — literature research CLI")
    parser.add_argument("--keywords", type=str, nargs="+", required=True, help="搜索关键词")
    parser.add_argument("--year-from", type=int, default=2025, help="检索起始年份（默认 2025）")
    args = parser.parse_args()

    run_cli(
        year_from=args.year_from,
        keywords=args.keywords,
    )


if __name__ == "__main__":
    main()
