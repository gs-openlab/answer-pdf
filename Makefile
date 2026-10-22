# 答卷构建流水线。
#
# 目录约定：assets/ 是 vendored 的只读资产（日志、原始截图、录屏）；build/ 是可再
# 生中间产物（插图、LaTeX 中间文件、main.pdf），已在 .gitignore 中忽略；源码目录
# 只留 main.tex / refs.bib / Makefile，脚本集中在 tools/。

ASSETS := assets
BUILD  := build

# 渲染参数集中在此，便于统一改字号 / 主题 / 折行
FONT_SIZE   := 11
LINE_HEIGHT := 1.35
THEME       := onedark
SCALE       := 2
# 统一折到 110 列：图片宽度一致，缩放进 PDF 后字号才一致，不会被宽行压小
WRAP        := 110

PYTHON    := python3
SHOT      := $(PYTHON) tools/ansi2png.py
SHOT_OPTS := --font-size $(FONT_SIZE) --line-height $(LINE_HEIGHT) \
             --theme $(THEME) --scale $(SCALE) --wrap $(WRAP)

# 正文引用的全部插图
PNGS := \
  $(BUILD)/t3-speed-cpu.png \
  $(BUILD)/t3-speed-metal.png \
  $(BUILD)/t3-speed-q2k.png \
  $(BUILD)/t3-server.png \
  $(BUILD)/t3-result.png \
  $(BUILD)/6-version.png \
  $(BUILD)/4-run.png \
  $(BUILD)/4-walk.png \
  $(BUILD)/4-gitdiff.png \
  $(BUILD)/6-run.png \
  $(BUILD)/6-ch2.png \
  $(BUILD)/6-basic.png \
  $(BUILD)/6-quote.png

.PHONY: all pdf pngs zip clean distclean
all: $(BUILD)/main.pdf
pdf: $(BUILD)/main.pdf
pngs: $(PNGS)

$(BUILD):
	@mkdir -p $@

# ---------------------------------------------------------------- 第 3 题截图
# 题目要「模型回答的第一句话 + 推理速度」。首句篇幅短、以文本为主，改用 LaTeX 代码
# 框录入（更清晰、可检索）；速度汇总统一截取各日志末 24 行成图，保证三种配置
# （CPU / Metal / Q2_K）截取口径一致。片段经管道送入（- 读 stdin）。
# 依赖里含 Makefile：改渲染参数（字号/主题/折行）或规则本身时能触发重生成。
$(BUILD)/t3-speed-cpu.png: $(ASSETS)/chat_log.txt tools/ansi2png.py Makefile | $(BUILD)
	@tail -24 $< | $(SHOT) - -o $@ --title 'llama-cli · 推理速度 (CPU)' $(SHOT_OPTS)

$(BUILD)/t3-speed-metal.png: $(ASSETS)/chat_log_metal.txt tools/ansi2png.py Makefile | $(BUILD)
	@tail -24 $< | $(SHOT) - -o $@ --title 'llama-cli · 推理速度 (Metal)' $(SHOT_OPTS)

$(BUILD)/t3-speed-q2k.png: $(ASSETS)/chat_log_q2_k.txt tools/ansi2png.py Makefile | $(BUILD)
	@tail -24 $< | $(SHOT) - -o $@ --title 'llama-cli · 推理速度 (Q2_K)' $(SHOT_OPTS)

# llama-server 后端日志（同样取末 24 行）
$(BUILD)/t3-server.png: $(ASSETS)/inference_log.txt tools/ansi2png.py Makefile | $(BUILD)
	@tail -24 $< | $(SHOT) - -o $@ --title 'llama-server · 后端日志' $(SHOT_OPTS)

# 接口返回的前端 JSON：交给 jq 做 indent=4 与语法着色（-C 输出 ANSI 色码），颜色与
# 缩进都由 jq 决定，避免手写高亮产生偏差；text 字段是单行逾千列的长字符串，同样折行
$(BUILD)/t3-result.png: $(ASSETS)/inference_result.txt tools/ansi2png.py Makefile | $(BUILD)
	@jq -C --indent 4 . $< | $(SHOT) - -o $@ --title 'curl · 接口返回 (JSON)' $(SHOT_OPTS)

# ---------------------------------------------------------------- 其他截图
# 第 4 题的运行截图取自录屏的两帧，第 6 题的运行截图是 macOS 终端里截下的原件（同框
# 含命令、宿主路径与版本信息）——这几张只做复制，保证 PDF 里的图与 assets/ 中的原图
# 逐字节一致
$(BUILD)/4-run.png:     $(ASSETS)/4-run.png     | $(BUILD)
	@cp $< $@
$(BUILD)/4-walk.png:    $(ASSETS)/4-walk.png    | $(BUILD)
	@cp $< $@
$(BUILD)/4-gitdiff.png: $(ASSETS)/4-1.png       | $(BUILD)
	@cp $< $@
$(BUILD)/6-run.png:     $(ASSETS)/6-1.png       | $(BUILD)
	@cp $< $@
$(BUILD)/6-ch2.png:     $(ASSETS)/6-2.png       | $(BUILD)
	@cp $< $@
$(BUILD)/6-basic.png:   $(ASSETS)/6-3.png       | $(BUILD)
	@cp $< $@
$(BUILD)/6-quote.png:   $(ASSETS)/6-4.png       | $(BUILD)
	@cp $< $@

# 版本信息由渲染得到，折行口径与第 3 题保持一致
$(BUILD)/6-version.png: $(ASSETS)/6-version.txt tools/ansi2png.py Makefile | $(BUILD)
	@$(SHOT) $< -o $@ --title 'rustc / cargo / rustup / qemu' $(SHOT_OPTS)

# ---------------------------------------------------------------- 编译
# 三遍 xelatex + 一遍 bibtex，全部产物只写 build/。首遍写 .aux（含 \bibdata /
# \citation）→ bibtex 生成 .bbl → 再两遍以定稿目录、交叉引用与参考文献编号。
$(BUILD)/main.pdf: main.tex refs.bib $(PNGS) Makefile | $(BUILD)
	@XELATEX='xelatex -interaction=nonstopmode -halt-on-error -output-directory=$(BUILD)'; \
	  $$XELATEX main.tex >/dev/null \
	    || { echo '编译失败：见 $(BUILD)/main.log' >&2; exit 1; }; \
	  bibtex $(BUILD)/main >/dev/null \
	    || { echo 'bibtex 失败：见 $(BUILD)/main.blg' >&2; exit 1; }; \
	  $$XELATEX main.tex >/dev/null \
	    && $$XELATEX main.tex >/dev/null \
	    || { echo '编译失败：见 $(BUILD)/main.log' >&2; exit 1; }; \
	  echo "PDF  -> $@"

# ---------------------------------------------------------------- 打包
# 交付包只含提交须知与题干点名的素材：答卷 PDF、LaTeX 源文件（main.tex /
# refs.bib）、第 3 题点名的完整 build_log.txt 与第 4 题的录屏 demo.mp4
# （PACKASSETS）。其余日志、截图已整幅嵌入 PDF 或随仓库提交，插图
# （build/*.png）由素材渲染而成，与构建脚本（Makefile / tools/）、README、仓库
# 元数据一样不入包；重新构建请克隆完整仓库后 make。
# 打包直接从工作区复制而非 git archive 导出：包即当前工作区状态，且打包脚本自身
# 不在包内，复制不存在自指问题。
# 压缩由 tools/make_zip.py 完成（用 zipfile 而非系统 zip，是为了给中文文件名写
# UTF-8 标志位，见该脚本注释）。
TOP := 202600540111_包晨文_Openlab初试
ZIP := $(TOP).zip
STAGE := $(BUILD)/pack-stage
# 题干点名的原件：第 3 题要求完整提交 build_log.txt；录屏本体 PDF 装不下
PACKASSETS := build_log.txt demo.mp4

.PHONY: zip
zip: $(ZIP)

$(ZIP): main.tex refs.bib $(BUILD)/main.pdf $(addprefix $(ASSETS)/,$(PACKASSETS)) tools/make_zip.py
	@rm -rf "$(STAGE)"
	@mkdir -p "$(STAGE)/$(TOP)/$(ASSETS)"
	@cp main.tex refs.bib "$(STAGE)/$(TOP)/"
	@cp $(BUILD)/main.pdf "$(STAGE)/$(TOP)/"
	@cp $(addprefix $(ASSETS)/,$(PACKASSETS)) "$(STAGE)/$(TOP)/$(ASSETS)/"
	@$(PYTHON) tools/make_zip.py "$(STAGE)/$(TOP)" "$@"
	@rm -rf "$(STAGE)"
	@echo "ZIP  -> $@  (顶层目录 $(TOP)/，直接复制工作区文件)"

clean: cleanroot
	@rm -rf $(BUILD) "$(ZIP)"
	@echo "移除 $(BUILD)/ 与 $(ZIP)"

# 默认情况下 xelatex 会在仓库根目录排版，会留下这些文件；build/ 才是现在的产物位置
cleanroot:
	@rm -f main.aux main.bbl main.blg main.fls main.fdb_latexmk main.log main.out \
	       main.pdf main.synctex.gz main.toc main.lof main.lot main.xdv
	@echo "移除仓库根目录的 LaTeX 中间文件"
