"""Reporte Markdown + JSON (+ Step Summary en GitHub Actions)."""
from __future__ import annotations

import datetime
import json
import os


class Report:
    def __init__(self) -> None:
        self.md: list[str] = []
        self.data: dict = {}

    # -- construcción ---------------------------------------------------
    def title(self, text: str) -> None:
        self.md.append(f"## {text}\n")

    def text(self, text: str) -> None:
        self.md.append(f"{text}\n")

    def table(self, title: str, headers: list[str], rows: list[list]) -> None:
        self.md.append(f"### {title}\n")
        self.md.append("| " + " | ".join(headers) + " |")
        self.md.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            self.md.append("| " + " | ".join(str(c) for c in row) + " |")
        self.md.append("")

    def kv(self, title: str, pairs: list[tuple]) -> None:
        self.table(title, ["Ajuste", "Valor"],
                   [[k, f"`{v}`"] for k, v in pairs])

    def details(self, title: str, body: str) -> None:
        self.md.append(f"<details><summary>{title}</summary>\n")
        self.md.append(body)
        self.md.append("</details>\n")

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def update(self, mapping: dict) -> None:
        self.data.update(mapping)

    # -- escritura ------------------------------------------------------
    def write(self, md_path: str, json_path: str,
              to_step_summary: bool = True) -> None:
        for path in (md_path, json_path):
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self.data.setdefault(
            "generado", datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds"))
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.md).rstrip() + "\n")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if to_step_summary:
            step = os.environ.get("GITHUB_STEP_SUMMARY")
            if step:
                try:
                    with open(step, "a", encoding="utf-8") as f:
                        f.write("\n".join(self.md) + "\n")
                except OSError:
                    pass
