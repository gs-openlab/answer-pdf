#!/usr/bin/env python3
"""打包交付 zip：把暂存目录装进压缩包。

用法: make_zip.py <暂存目录> <输出 zip>

用标准库 zipfile 而非系统 zip 命令，是因为包内顶层目录是中文
（202600540111_包晨文_Openlab初试）：ZIP 规范要求非 ASCII 文件名置 UTF-8
标志位（bit 11），macOS 自带的 Info-ZIP 3.0 不支持 -UN=UTF8 且默认不置位，
Windows 解压会乱码；zipfile 对非 ASCII 名自动置位。
"""
import os
import sys
import zipfile


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("用法: make_zip.py <暂存目录> <输出 zip>")
    src, out = sys.argv[1], sys.argv[2]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(src):
            dirs.sort()
            rel = os.path.relpath(root, os.path.dirname(src))
            z.writestr(rel + "/", "")  # 显式写入目录项，空目录也不丢
            for f in sorted(files):
                z.write(os.path.join(root, f), os.path.join(rel, f))


if __name__ == "__main__":
    main()
