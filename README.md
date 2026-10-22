# 纳新题作答

纳新题第 1 题的作答仓库：把其余各题的作答排版成一份 PDF。

- `main.tex` —— 答卷正文（XeLaTeX + ctex）
- `refs.bib` —— 参考文献库
- `Makefile` —— 构建流水线：`make` 生成 `build/main.pdf`（按依赖增量，只重跑输入有变化的步骤）；`make zip` 打交付包；`make clean` 清理
- `tools/ansi2png.py` —— 把终端日志渲染成截图（插图与文同源）
- `tools/make_zip.py` —— 打交付包（zipfile 保证中文文件名的 UTF-8 标志位）
- `assets/` —— 各题的只读原始素材（日志、截图、录屏），是 `build/` 的输入

## 构建

```bash
make        # 生成 build/main.pdf
make zip    # 打交付包 202600540111_包晨文_Openlab初试.zip
make clean  # 清理 build/ 与 zip
```

依赖：MacTex、Python 3、jq、各题仓库的产物。

## 各题仓库

- 第 3 题：<https://github.com/gs-openlab/llama.cpp-build-run>
- 第 4 题：<https://github.com/gs-openlab/desktop-pet>
- 第 7 题：<https://github.com/gs-openlab/strange-book>

## AI 使用披露

本仓库未使用智能体编码。
