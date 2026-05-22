# Word Template Skill

这是一个“Word 模板驱动内容生成”工具。它会读取用户提供的 `.docx` 模板，自动识别各级标题，把标题当作锚点，逐节调用 DeepSeek OpenAI 兼容接口生成正文，然后把正文插入到对应标题之后。

核心原则：不从零创建 Word 文件，而是复制模板并在副本上原位插入内容，以尽量保留页面设置、页眉页脚、页码、标题样式、正文格式、表格、图片、分节符和原有空白结构。

## 安装

```bash
cd word_template_skill
python -m pip install -r requirements.txt
```

Python 版本要求：3.10 或更高。

## 设置 DeepSeek Key

推荐在项目目录创建 `.env`：

```bash
DEEPSEEK_API_KEY=你的DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

也可以直接使用系统环境变量。API Key 不会写入代码，也不会写入生成的 Word 文件。

## 运行

```bash
python main.py \
  --template "模板.docx" \
  --topic "一种基于多模态数据融合的设备故障诊断方法" \
  --output "生成结果.docx"
```

可选参数：

```bash
python main.py \
  --template "模板.docx" \
  --topic "一种基于多模态数据融合的设备故障诊断方法" \
  --output "生成结果.docx" \
  --model deepseek-v4-pro \
  --temperature 0.3 \
  --max-tokens 4096 \
  --section-mode auto \
  --overwrite false
```

`--overwrite false` 是默认值。如果输出文件已存在，工具会自动生成 `生成结果_1.docx` 这类新文件名。输出路径不允许和模板路径相同。

## 标题识别

`--section-mode auto` 默认先识别 Word 内置标题样式：

- `Heading 1`, `Heading 2`, `Heading 3`
- `标题 1`, `标题 2`, `标题 3`

如果模板没有使用标准标题样式，则按文本规则识别：

- `一、xxx`, `二、xxx`, `三、xxx`
- `（一）xxx`, `（二）xxx`
- `1. xxx`, `1.1 xxx`
- `第1章 xxx`, `第一章 xxx`
- `技术领域`, `背景技术`, `发明内容`, `附图说明`, `具体实施方式`, `权利要求书`, `摘要`

也可以手动指定：

- `--section-mode style`: 只识别 Word 标题样式
- `--section-mode text`: 只识别文本规则标题
- `--section-mode all`: 合并两类规则

## 写入策略

工具会先复制模板到临时输出文件，再打开这个副本进行修改。每个章节的正文只插入到识别到的标题段落之后，不会统一追加到文档末尾。

插入正文时会优先继承当前标题后第一个正文段落的样式、段落格式和字体属性。如果找不到当前章节正文段落，会回退到全文第一个正文段落，再回退到 `Normal` 样式。

如果标题下第一个有效段落是 `【请填写】`、`XXX`、`此处填写`、`待补充`、`......` 等占位符，会替换该占位符。否则会在标题后新增正文段落，并保留模板原有示例文字。

## 日志

运行后会生成两个日志文件：

- `生成结果.log.json`: 结构化 JSON 日志，包含标题列表、每节生成状态、插入段落数、Prompt、Response、Token usage 和错误信息。
- `生成结果.run.log`: 人类可读运行日志。

JSON 日志示例字段：

```json
{
  "template": "...",
  "output": "...",
  "topic": "...",
  "sections": [
    {
      "title": "技术领域",
      "level": 1,
      "status": "inserted",
      "reason": "",
      "inserted_paragraphs": 2
    }
  ]
}
```

## 常见问题

### 标题识别不到怎么办

优先给模板标题套用 Word 的 `标题 1/2/3` 或 `Heading 1/2/3` 样式。也可以尝试 `--section-mode text` 或 `--section-mode all`。如果模板标题完全没有编号、样式或固定短标题，建议先在模板里增加标准标题样式。

### 模板格式变了怎么办

工具不会重建文档，而是在模板副本上插入段落。若局部正文格式不理想，通常是标题下没有可参考的正文段落。可以在模板对应标题后放一个格式正确的占位段落，例如 `【请填写】`，工具会替换它并继承格式。

### 内容插错位置怎么办

查看 `*.log.json` 里的 `headings` 列表，确认 `paragraph_index` 和标题识别结果是否正确。如果文本规则误识别了正文，改用 `--section-mode style`，并给真正标题设置 Word 标题样式。

### 如何跳过某些章节

封面、目录、参考文献、致谢默认跳过。额外跳过章节可使用：

```bash
python main.py --template "模板.docx" --topic "..." --output "结果.docx" --skip-sections "附录,术语表"
```

### 如何处理目录

工具不会手动改写目录字段，避免破坏 Word 的目录域。生成文档后，在 Word 中右键目录并选择“更新域”或“更新整个目录”。

### 如何更新目录字段

在 Microsoft Word 中打开输出文件，点击目录，选择“更新目录”。如果文档使用自动目录，建议选择“更新整个目录”。

## 设计边界

本工具尽量复制正文段落样式、字体、缩进、段前段后和行距。复杂编号、多级列表和某些自定义域由 Word 内部关系维护，工具会避免强行改写，以降低破坏模板格式的风险。
