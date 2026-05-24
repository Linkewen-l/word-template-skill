# Word Template Skill

Generate Chinese Word documents from `.docx` templates by detecting section headings, calling a DeepSeek-compatible API section by section, and inserting the generated body text back into the copied template.

The project keeps the original template structure as much as possible instead of rebuilding the document from scratch. That helps preserve page settings, headers and footers, section breaks, fonts, paragraph styles, and placeholder layout.

## Workspace Layout

The current CLI defaults assume a workspace shaped like this:

```text
your-workspace/
  templates/
  topics/
  word-template-skill/
    word_template_skill/
```

- `templates/`: reusable `.docx` templates
- `topics/`: one folder per research topic or assignment
- `word-template-skill/`: this repository

If your folders live somewhere else, use `--template-dir` and `--topic-root` to override the defaults.

The repository can also track reusable templates under `templates/` for backup and sharing. Topic workspaces under `topics/` are intentionally kept local and are not committed.

## Quick Start

Install dependencies:

```bash
cd word_template_skill
python -m pip install -r requirements.txt
```

Create a `.env` file in `word_template_skill/`:

```bash
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

Run with a template from the template library:

```bash
python main.py \
  --template-name "作业模板" \
  --topic "现代信号处理方法在滚动轴承变转速故障诊断中的应用研究"
```

This creates a topic workspace like:

```text
topics/现代信号处理方法在滚动轴承变转速故障诊断中的应用研究/
  materials/
  notes/
  outputs/
```

Generated `.docx`, `.log.json`, and `.run.log` files are written under `outputs/`.

## Main Features

- Detect headings from Word heading styles or common Chinese heading text
- Support template library mode with `--template-name`
- Auto-create a per-topic workspace for each new research topic
- Preserve template formatting by inserting content in place
- Skip non-body sections such as `目录` and `参考文献`
- Write structured JSON logs for debugging heading detection and generation status

## Supported Patent-Like Headings

The text detector recognizes headings such as:

- `说明书摘要`
- `摘要附图`
- `权利要求书`
- `技术领域`
- `技术背景`
- `背景技术`
- `发明内容`
- `附图说明`
- `具体实施方式`
- `摘要`

Trailing `:` or `：` is ignored during detection.

## More Details

Detailed usage notes live in [word_template_skill/README.md](word_template_skill/README.md).
