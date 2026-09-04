"""Shared product-help resources."""

from pathlib import Path


def product_guide() -> str:
    return (Path(__file__).with_name("product_guide.md")).read_text(encoding="utf-8")
