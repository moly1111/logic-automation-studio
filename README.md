# Logic Automation Studio

一个可编辑的本地自动化脚本引擎（MVP），支持通过图形界面编排流程，并执行多种动作与条件判断。

## 功能概览

- 点击动作：`ClickText` / `ClickPosition` / `ClickImage`
- 输入动作：`TypeText` / `KeyPress`
- 控制动作：`Wait` / `IfElse`（`on_true` / `on_false`）
- 稳定性：重试、失败跳转、运行日志、流程图预览
- 坐标处理：支持 DPI 场景，图片识别默认可全屏自适应

## 目录结构

- `automation_studio.py`：主界面（流程编辑与运行）
- `automation_runtime.py`：运行时引擎
- `click_actions/`：点击与输入动作能力
- `ocr/`：OCR 入口封装
- `engine/`、`ui/`：分层导出模块

## 配置说明（私密）

项目使用 `set_ocr.txt` 存放 OCR/翻译密钥等敏感信息。  
该文件已在 `.gitignore` 中排除，不会被提交到 Git。

请按 `set_ocr.example.txt` 复制一份：

```bash
cp set_ocr.example.txt set_ocr.txt
```

然后填写你自己的密钥信息。

## 运行方式

双击根目录 `start.bat` 启动主界面。

## 依赖建议

- Python 3.10+
- `requests`
- `pillow`
- `opencv-python`
- `numpy`

