#!/usr/bin/env python3
"""递增 version.json 中的 build 号（在 git commit 前执行）。"""
from src.core.config import bump_build

if __name__ == "__main__":
    new_build = bump_build()
    print(f"build → {new_build}")
