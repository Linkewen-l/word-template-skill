# Usage Example

```bash
cd word_template_skill
python -m pip install -r requirements.txt
```

Create `.env`:

```bash
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

Run:

```bash
python main.py \
  --template-name "专利模板" \
  --topic "一种基于多模态数据融合的设备故障诊断方法" \
  --section-mode auto \
  --temperature 0.3 \
  --max-tokens 4096
```

With the default workspace layout:

```text
your-workspace/
  templates/
  topics/
  word-template-skill/
```

the command reads `templates/专利模板.docx` and writes outputs/logs under:

```text
topics/一种基于多模态数据融合的设备故障诊断方法/outputs/
```

You can also override the default directories:

```bash
python main.py \
  --template-name "作业模板" \
  --template-dir "/path/to/templates" \
  --topic "现代信号处理方法在滚动轴承变转速故障诊断中的应用研究" \
  --topic-root "/path/to/topics"
```

Explicit path mode is still supported:

```bash
python main.py \
  --template "../专利模板.docx" \
  --topic "一种基于多模态数据融合的设备故障诊断方法" \
  --output "../生成结果.docx" \
  --section-mode auto \
  --temperature 0.3 \
  --max-tokens 4096 \
  --overwrite false
```

After completion, inspect:

- `../生成结果.docx`
- `../生成结果.log.json`
- `../生成结果.run.log`

If headings are not detected, try:

```bash
python main.py \
  --template "../专利模板.docx" \
  --topic "一种基于多模态数据融合的设备故障诊断方法" \
  --output "../生成结果.docx" \
  --section-mode all
```
