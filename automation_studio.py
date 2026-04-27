import json
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

from engine.runtime import AutomationEngine, RuntimeActions
from ocr.youdao_locator import YoudaoTextLocator


FLOW_TEMPLATE: Dict[str, Any] = {
    "start": "step1",
    "steps": [
        {"id": "step1", "type": "KeyPress", "params": {"key": "1"}, "next": "step2"},
        {"id": "step2", "type": "KeyPress", "params": {"key": "2"}, "next": "step3"},
        {
            "id": "step3",
            "type": "IfElse",
            "condition": {"type": "ExistsText", "params": {"text": "成功", "timeout_sec": 3}},
            "on_true": "step4",
            "on_false": "STOP",
        },
        {
            "id": "step4",
            "type": "ClickText",
            "params": {"text": "发送", "exact_match": True, "click_times": 1},
            "next": "STOP",
        },
    ],
}

STEP_PARAM_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "ClickText": {
        "text": "发送",
        "exact_match": True,
        "case_sensitive": False,
        "delay_sec": 0,
        "click_times": 1,
        "click_interval_sec": 0.08,
    },
    "ClickPosition": {
        "x": 1200,
        "y": 800,
        "delay_sec": 0,
        "click_times": 1,
        "click_interval_sec": 0.08,
    },
    "ClickImage": {
        "template_path": "assets/sample.png",
        "confidence": 0.85,
        "delay_sec": 0,
        "click_times": 1,
        "click_interval_sec": 0.08,
    },
    "KeyPress": {
        "key": "1",
        "times": 1,
        "interval_sec": 0.05,
        "delay_sec": 0,
    },
    "Wait": {
        "seconds": 1,
    },
    "TypeText": {
        "content": "你好，这是一段自动输入的文字。",
        "delay_sec": 0,
        "interval_sec": 0,
        "press_enter": False,
    },
    "IfElse": {},
}

IFELSE_CONDITION_TEMPLATE: Dict[str, Any] = {
    "type": "ExistsText",
    "params": {
        "text": "成功",
        "timeout_sec": 3,
        "interval_sec": 0.4,
        "exact_match": True,
        "case_sensitive": False,
    },
}


class AutomationStudio:
    def __init__(self) -> None:
        # 先启用 DPI 感知，再创建 Tk 窗口，避免运行后窗口缩放跳变。
        YoudaoTextLocator.enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.title("逻辑自动化脚本编辑引擎 (MVP)")
        self.default_geometry = "1360x1080"
        self.min_width = 1240
        self.min_height = 900
        self.root.geometry(self.default_geometry)
        self.root.minsize(self.min_width, self.min_height)

        self.flow: Dict[str, Any] = json.loads(json.dumps(FLOW_TEMPLATE))
        self.current_index: Optional[int] = None
        self.running = False
        self.worker: Optional[threading.Thread] = None
        self.engine: Optional[AutomationEngine] = None

        self.start_var = tk.StringVar(value=self.flow.get("start", ""))
        self.step_id_var = tk.StringVar()
        self.step_type_var = tk.StringVar(value="ClickText")
        self.next_var = tk.StringVar(value="STOP")
        self.on_error_var = tk.StringVar(value="STOP")
        self.retry_var = tk.StringVar(value="0")
        self.retry_interval_var = tk.StringVar(value="0.3")
        self.post_delay_var = tk.StringVar(value="0")
        self.on_true_var = tk.StringVar(value="STOP")
        self.on_false_var = tk.StringVar(value="STOP")

        self.params_text: Optional[tk.Text] = None
        self.condition_text: Optional[tk.Text] = None
        self.log_text: Optional[tk.Text] = None
        self.step_list: Optional[tk.Listbox] = None
        self.param_form_frame: Optional[ttk.Frame] = None
        self.param_controls: Dict[str, Any] = {}

        self._build_ui()
        self.refresh_step_list()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("TLabelframe", padding=8)
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))

        container = ttk.Frame(self.root, padding=10)
        container.pack(fill="both", expand=True)

        paned = ttk.Panedwindow(container, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=(0, 0, 8, 0))
        right = ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        left_box = ttk.LabelFrame(left, text="步骤列表")
        left_box.pack(fill="both", expand=True)

        self.step_list = tk.Listbox(left_box, width=30, height=26, activestyle="none")
        self.step_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.step_list.bind("<<ListboxSelect>>", self.on_select_step)

        btns = ttk.Frame(left_box)
        btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btns, text="新增", command=self.add_step).grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(btns, text="删除", command=self.delete_step).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(btns, text="上移", command=lambda: self.move_step(-1)).grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(btns, text="下移", command=lambda: self.move_step(1)).grid(row=1, column=1, padx=2, pady=2)

        io_btns = ttk.Frame(left_box)
        io_btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(io_btns, text="导入JSON", command=self.import_flow).grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(io_btns, text="导出JSON", command=self.export_flow).grid(row=0, column=1, padx=2, pady=2)

        run_btns = ttk.Frame(left_box)
        run_btns.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Button(run_btns, text="运行", command=self.run_flow).grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(run_btns, text="停止", command=self.stop_flow).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(run_btns, text="保存步骤", command=self.save_current_step).grid(row=1, column=0, padx=2, pady=2, sticky="we")
        ttk.Button(run_btns, text="自动搭建JSON骨架", command=self.apply_json_template).grid(
            row=1, column=1, padx=2, pady=2, sticky="we"
        )
        ttk.Button(run_btns, text="查看流程图", command=self.show_flow_diagram).grid(
            row=2, column=0, columnspan=2, padx=2, pady=2, sticky="we"
        )
        run_btns.columnconfigure(0, weight=1)
        run_btns.columnconfigure(1, weight=1)

        info = ttk.Label(
            left_box,
            text="提示：先选中步骤再编辑右侧字段（点击可设 click_times 或 double_click）",
            foreground="#666666",
        )
        info.pack(anchor="w", padx=8, pady=(0, 8))

        top_right = ttk.LabelFrame(right, text="步骤编辑")
        top_right.pack(fill="x")
        editor = ttk.Frame(top_right)
        editor.pack(fill="x", padx=6, pady=4)
        editor.columnconfigure(3, weight=1)

        ttk.Label(editor, text="流程起点ID").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.start_var, width=16).grid(row=0, column=1, sticky="w", padx=(8, 16), pady=4)
        ttk.Label(editor, text="步骤ID").grid(row=0, column=2, sticky="w")
        ttk.Entry(editor, textvariable=self.step_id_var, width=18).grid(row=0, column=3, sticky="we", padx=(8, 0), pady=4)

        ttk.Label(editor, text="步骤类型").grid(row=1, column=0, sticky="w")
        type_box = ttk.Combobox(
            editor,
            textvariable=self.step_type_var,
            values=["ClickText", "ClickPosition", "ClickImage", "TypeText", "KeyPress", "Wait", "IfElse"],
            width=14,
            state="readonly",
        )
        type_box.grid(row=1, column=1, sticky="w", padx=(8, 16), pady=4)
        type_box.bind("<<ComboboxSelected>>", self.on_step_type_changed)
        ttk.Label(editor, text="成功后下一步").grid(row=1, column=2, sticky="w")
        ttk.Entry(editor, textvariable=self.next_var, width=18).grid(row=1, column=3, sticky="we", padx=(8, 0), pady=4)

        ttk.Label(editor, text="失败跳转").grid(row=2, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.on_error_var, width=16).grid(row=2, column=1, sticky="w", padx=(8, 16), pady=4)
        ttk.Label(editor, text="重试次数").grid(row=2, column=2, sticky="w")
        ttk.Entry(editor, textvariable=self.retry_var, width=18).grid(row=2, column=3, sticky="we", padx=(8, 0), pady=4)

        ttk.Label(editor, text="重试间隔(秒)").grid(row=3, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.retry_interval_var, width=16).grid(row=3, column=1, sticky="w", padx=(8, 16), pady=4)
        ttk.Label(editor, text="后置延迟(秒)").grid(row=3, column=2, sticky="w")
        ttk.Entry(editor, textvariable=self.post_delay_var, width=18).grid(row=3, column=3, sticky="we", padx=(8, 0), pady=4)

        ttk.Label(editor, text="条件为真跳转").grid(row=4, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.on_true_var, width=16).grid(row=4, column=1, sticky="w", padx=(8, 16), pady=4)
        ttk.Label(editor, text="条件为假跳转").grid(row=4, column=2, sticky="w")
        ttk.Entry(editor, textvariable=self.on_false_var, width=18).grid(row=4, column=3, sticky="we", padx=(8, 0), pady=4)

        bottom_right = ttk.Frame(right)
        bottom_right.pack(fill="both", expand=True, pady=(8, 0))
        bottom_right.columnconfigure(0, weight=1)
        bottom_right.rowconfigure(0, weight=3)
        bottom_right.rowconfigure(1, weight=2)

        json_box = ttk.LabelFrame(bottom_right, text="参数编辑")
        json_box.grid(row=0, column=0, sticky="nsew")
        json_box.columnconfigure(0, weight=1)
        json_box.columnconfigure(1, weight=1)
        json_box.rowconfigure(1, weight=1)
        json_box.rowconfigure(2, weight=1)

        self.param_form_frame = ttk.Frame(json_box)
        self.param_form_frame.grid(row=0, column=0, columnspan=2, sticky="we", padx=6, pady=(4, 2))
        self.param_form_frame.columnconfigure(1, weight=1)
        self.param_form_frame.columnconfigure(3, weight=1)

        ttk.Label(json_box, text="步骤参数 JSON").grid(row=1, column=0, sticky="nw", padx=(6, 8), pady=6)
        self.params_text = tk.Text(json_box, width=50, height=8)
        self.params_text.grid(row=1, column=1, sticky="nsew", padx=(0, 6), pady=6)

        ttk.Label(json_box, text="条件参数 JSON（仅IfElse）").grid(row=2, column=0, sticky="nw", padx=(6, 8), pady=6)
        self.condition_text = tk.Text(json_box, width=50, height=6)
        self.condition_text.grid(row=2, column=1, sticky="nsew", padx=(0, 6), pady=6)

        log_box = ttk.LabelFrame(bottom_right, text="运行日志")
        log_box.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_box, height=8)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

    def log(self, msg: str) -> None:
        if not self.log_text:
            return
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")

    def _hide_window(self) -> None:
        self.root.withdraw()

    def _show_window(self) -> None:
        # 避免隐藏恢复后窗口尺寸异常变小，导致控件折叠。
        try:
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            if width < self.min_width or height < self.min_height:
                self.root.geometry(self.default_geometry)
        except Exception:
            self.root.geometry(self.default_geometry)
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def refresh_step_list(self) -> None:
        if not self.step_list:
            return
        self.step_list.delete(0, "end")
        for s in self.flow.get("steps", []):
            self.step_list.insert("end", f"{s.get('id')} | {s.get('type')}")

    def get_selected_index(self) -> Optional[int]:
        if not self.step_list:
            return None
        sel = self.step_list.curselection()
        return sel[0] if sel else None

    def on_select_step(self, _: Any = None) -> None:
        idx = self.get_selected_index()
        if idx is None:
            return
        self.current_index = idx
        step = self.flow["steps"][idx]
        self.step_id_var.set(step.get("id", ""))
        self.step_type_var.set(step.get("type", "ClickText"))
        self.next_var.set(step.get("next", "STOP"))
        self.on_error_var.set(step.get("on_error", "STOP"))
        self.retry_var.set(str(step.get("retry_count", 0)))
        self.retry_interval_var.set(str(step.get("retry_interval_sec", 0.3)))
        self.post_delay_var.set(str(step.get("post_delay_sec", 0)))
        self.on_true_var.set(step.get("on_true", "STOP"))
        self.on_false_var.set(step.get("on_false", "STOP"))
        if self.params_text:
            self.params_text.delete("1.0", "end")
            self.params_text.insert("1.0", json.dumps(step.get("params", {}), ensure_ascii=False, indent=2))
        if self.condition_text:
            self.condition_text.delete("1.0", "end")
            self.condition_text.insert("1.0", json.dumps(step.get("condition", {}), ensure_ascii=False, indent=2))
        self.render_param_inputs()

    def save_current_step(self) -> None:
        idx = self.current_index
        if idx is None:
            messagebox.showwarning("提示", "请先选择一个步骤")
            return
        try:
            self.sync_params_from_controls_to_json()
            params_obj = json.loads((self.params_text.get("1.0", "end").strip() if self.params_text else "{}") or "{}")
            cond_obj = json.loads((self.condition_text.get("1.0", "end").strip() if self.condition_text else "{}") or "{}")
            step = {
                "id": self.step_id_var.get().strip(),
                "type": self.step_type_var.get().strip(),
                "next": self.next_var.get().strip() or "STOP",
                "on_error": self.on_error_var.get().strip() or "STOP",
                "retry_count": int(self.retry_var.get().strip() or "0"),
                "retry_interval_sec": float(self.retry_interval_var.get().strip() or "0.3"),
                "post_delay_sec": float(self.post_delay_var.get().strip() or "0"),
                "params": params_obj,
                "on_true": self.on_true_var.get().strip() or "STOP",
                "on_false": self.on_false_var.get().strip() or "STOP",
                "condition": cond_obj,
            }
            self.flow["steps"][idx] = step
            self.flow["start"] = self.start_var.get().strip() or self.flow["steps"][0]["id"]
            self.refresh_step_list()
            self.step_list.selection_set(idx)
            self.log(f"已保存步骤: {step['id']}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def on_step_type_changed(self, _: Any = None) -> None:
        self.apply_json_template()

    def apply_json_template(self) -> None:
        stype = self.step_type_var.get().strip() or "ClickText"
        params_template = STEP_PARAM_TEMPLATES.get(stype, {})
        if self.params_text:
            self.params_text.delete("1.0", "end")
            self.params_text.insert("1.0", json.dumps(params_template, ensure_ascii=False, indent=2))
        if self.condition_text:
            self.condition_text.delete("1.0", "end")
            if stype == "IfElse":
                self.condition_text.insert(
                    "1.0",
                    json.dumps(IFELSE_CONDITION_TEMPLATE, ensure_ascii=False, indent=2),
                )
            else:
                self.condition_text.insert("1.0", "{}")
        self.render_param_inputs()
        self.log(f"已自动搭建 {stype} 的 JSON 骨架")

    def render_param_inputs(self) -> None:
        if not self.param_form_frame:
            return
        for child in self.param_form_frame.winfo_children():
            child.destroy()
        self.param_controls = {}

        stype = self.step_type_var.get().strip() or "ClickText"
        params = {}
        if self.params_text:
            raw = self.params_text.get("1.0", "end").strip()
            if raw:
                try:
                    params = json.loads(raw)
                except Exception:
                    params = {}

        def add_label_entry(row: int, col: int, label: str, key: str, default: Any = "") -> None:
            ttk.Label(self.param_form_frame, text=label).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=2)
            var = tk.StringVar(value=str(params.get(key, default)))
            entry = ttk.Entry(self.param_form_frame, textvariable=var, width=18)
            entry.grid(row=row, column=col + 1, sticky="we", padx=(0, 10), pady=2)
            self.param_controls[key] = ("str", var)

        def add_check(row: int, col: int, label: str, key: str, default: bool = False) -> None:
            var = tk.BooleanVar(value=bool(params.get(key, default)))
            chk = ttk.Checkbutton(self.param_form_frame, text=label, variable=var)
            chk.grid(row=row, column=col, sticky="w", padx=(0, 10), pady=2)
            self.param_controls[key] = ("bool", var)

        if stype == "ClickText":
            add_label_entry(0, 0, "目标文字", "text", "发送")
            add_label_entry(0, 2, "延迟(秒)", "delay_sec", 0)
            add_label_entry(1, 0, "点击次数", "click_times", 1)
            add_label_entry(1, 2, "点击间隔", "click_interval_sec", 0.08)
            add_check(2, 0, "精确匹配", "exact_match", True)
            add_check(2, 2, "区分大小写", "case_sensitive", False)
        elif stype == "ClickPosition":
            add_label_entry(0, 0, "坐标 X", "x", 1200)
            add_label_entry(0, 2, "坐标 Y", "y", 800)
            add_label_entry(1, 0, "延迟(秒)", "delay_sec", 0)
            add_label_entry(1, 2, "点击次数", "click_times", 1)
            add_label_entry(2, 0, "点击间隔", "click_interval_sec", 0.08)
        elif stype == "ClickImage":
            ttk.Label(self.param_form_frame, text="模板图片").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=2)
            path_var = tk.StringVar(value=str(params.get("template_path", "assets/sample.png")))
            path_entry = ttk.Entry(self.param_form_frame, textvariable=path_var, width=42)
            path_entry.grid(row=0, column=1, columnspan=2, sticky="we", padx=(0, 6), pady=2)
            self.param_form_frame.columnconfigure(2, weight=1)

            def choose_image() -> None:
                p = filedialog.askopenfilename(
                    title="选择模板图片",
                    filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.bmp;*.webp"), ("所有文件", "*.*")],
                )
                if p:
                    path_var.set(p)

            ttk.Button(self.param_form_frame, text="选择图片", command=choose_image).grid(
                row=0, column=3, sticky="we", padx=(0, 8), pady=2
            )
            self.param_controls["template_path"] = ("str", path_var)
            add_label_entry(1, 0, "置信度", "confidence", 0.85)
            add_label_entry(1, 2, "延迟(秒)", "delay_sec", 0)
            add_label_entry(2, 0, "点击次数", "click_times", 1)
            add_label_entry(2, 2, "点击间隔", "click_interval_sec", 0.08)
            if "region" in params and params["region"] not in (None, [], ""):
                rd = params["region"]
                region_text = ",".join(str(v) for v in rd) if isinstance(rd, list) else str(rd)
            else:
                region_text = ""
            add_label_entry(3, 0, "识别区域(留空=全屏)", "region_str", region_text)
        elif stype == "TypeText":
            ttk.Label(self.param_form_frame, text="要输入的文字").grid(row=0, column=0, sticky="nw", padx=(0, 6), pady=4)
            body = tk.Text(self.param_form_frame, height=5, width=50, wrap="word", font=("Microsoft YaHei UI", 9))
            body.grid(row=0, column=1, columnspan=3, sticky="nsew", padx=(0, 8), pady=4)
            self.param_form_frame.rowconfigure(0, weight=1)
            self.param_form_frame.columnconfigure(1, weight=1)
            initial = str(params.get("content", params.get("text", "")))
            body.insert("1.0", initial)
            self.param_controls["content"] = ("multiline", body)
            add_label_entry(1, 0, "开始延迟(秒)", "delay_sec", 0)
            add_label_entry(1, 2, "字符间隔(秒)", "interval_sec", 0)
            add_check(2, 0, "末尾按回车", "press_enter", False)
        elif stype == "KeyPress":
            add_label_entry(0, 0, "按键", "key", "1")
            add_label_entry(0, 2, "次数", "times", 1)
            add_label_entry(1, 0, "按键间隔", "interval_sec", 0.05)
            add_label_entry(1, 2, "延迟(秒)", "delay_sec", 0)
        elif stype == "Wait":
            add_label_entry(0, 0, "等待秒数", "seconds", 1)
        else:
            ttk.Label(self.param_form_frame, text="IfElse 无步骤参数，请编辑下方条件参数 JSON").grid(
                row=0, column=0, columnspan=4, sticky="w", padx=(0, 6), pady=2
            )

    def sync_params_from_controls_to_json(self) -> None:
        if not self.params_text:
            return
        stype = self.step_type_var.get().strip() or "ClickText"
        current = {}
        raw = self.params_text.get("1.0", "end").strip()
        if raw:
            try:
                current = json.loads(raw)
            except Exception:
                current = {}
        if stype == "IfElse":
            return
        result: Dict[str, Any] = {}
        for key, value in self.param_controls.items():
            mode, var = value
            if mode == "multiline":
                if key == "content":
                    result["content"] = var.get("1.0", "end").rstrip("\n\r")
                continue
            if key == "region_str":
                text = str(var.get()).strip().lower()
                if text in ("", "auto", "full", "fullscreen", "all", "*"):
                    # 不写 region：引擎按虚拟全屏自适配，换电脑无需改分辨率
                    continue
                parts = [p.strip() for p in str(var.get()).split(",") if p.strip()]
                if len(parts) == 4:
                    try:
                        result["region"] = [int(float(v)) for v in parts]
                    except Exception:
                        pass
                continue
            if mode == "bool":
                result[key] = bool(var.get())
            else:
                text = str(var.get()).strip()
                if key in ("x", "y", "times", "click_times"):
                    result[key] = int(float(text or "0"))
                elif key in ("delay_sec", "click_interval_sec", "confidence", "interval_sec", "seconds"):
                    result[key] = float(text or "0")
                else:
                    result[key] = text
        self.params_text.delete("1.0", "end")
        self.params_text.insert("1.0", json.dumps(result, ensure_ascii=False, indent=2))

    def add_step(self) -> None:
        steps: List[Dict[str, Any]] = self.flow["steps"]
        new_id = f"step{len(steps)+1}"
        steps.append({"id": new_id, "type": "ClickText", "params": {"text": "发送"}, "next": "STOP"})
        self.refresh_step_list()
        self.step_list.selection_clear(0, "end")
        self.step_list.selection_set(len(steps) - 1)
        self.on_select_step()

    def delete_step(self) -> None:
        idx = self.get_selected_index()
        if idx is None:
            return
        self.flow["steps"].pop(idx)
        self.refresh_step_list()
        if self.flow["steps"]:
            nxt = min(idx, len(self.flow["steps"]) - 1)
            self.step_list.selection_set(nxt)
            self.on_select_step()

    def move_step(self, delta: int) -> None:
        idx = self.get_selected_index()
        if idx is None:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self.flow["steps"]):
            return
        steps = self.flow["steps"]
        steps[idx], steps[new_idx] = steps[new_idx], steps[idx]
        self.refresh_step_list()
        self.step_list.selection_set(new_idx)
        self.on_select_step()

    def import_flow(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not path:
            return
        try:
            data = json.load(open(path, "r", encoding="utf-8"))
            if "steps" not in data or not isinstance(data["steps"], list):
                raise ValueError("无效流程文件，缺少 steps")
            self.flow = data
            self.start_var.set(self.flow.get("start", ""))
            self.refresh_step_list()
            self.log(f"已导入: {path}")
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def export_flow(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            self.flow["start"] = self.start_var.get().strip() or (self.flow["steps"][0]["id"] if self.flow["steps"] else "")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.flow, f, ensure_ascii=False, indent=2)
            self.log(f"已导出: {path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def run_flow(self) -> None:
        if self.running:
            self.log("流程已在运行中")
            return
        if self.current_index is not None:
            self.save_current_step()
        self.running = True
        self._hide_window()
        self.log("=== 开始运行 ===")
        runtime = RuntimeActions(config_path="set_ocr.txt", logger=self.log)
        self.engine = AutomationEngine(runtime=runtime, logger=self.log)

        def worker() -> None:
            try:
                result = self.engine.run_flow(self.flow)
                self.log(f"=== 结束: ok={result.ok}, msg={result.message}, step={result.step_id} ===")
            except Exception as e:
                self.log(f"=== 运行异常: {e} ===")
            finally:
                self.running = False
                self.root.after(0, self._show_window)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def stop_flow(self) -> None:
        if self.engine:
            self.engine.request_stop()
            self.log("已请求停止")

    def _get_step_display_name(self, step: Dict[str, Any]) -> str:
        sid = step.get("id", "")
        stype = step.get("type", "")
        return f"{sid}\\n{stype}"

    @staticmethod
    def _draw_arrow_with_label(
        canvas: tk.Canvas,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        label: str,
        color: str = "#4A4A4A",
        dash: Optional[tuple[int, int]] = None,
    ) -> None:
        canvas.create_line(x1, y1, x2, y2, arrow=tk.LAST, fill=color, width=2, dash=dash)
        lx = int((x1 + x2) / 2)
        ly = int((y1 + y2) / 2) - 8
        canvas.create_text(lx, ly, text=label, fill=color, font=("Microsoft YaHei UI", 9, "bold"))

    def show_flow_diagram(self) -> None:
        if self.current_index is not None:
            self.save_current_step()

        steps = self.flow.get("steps", [])
        if not steps:
            messagebox.showinfo("流程图", "当前没有可绘制的步骤")
            return

        step_map: Dict[str, Dict[str, Any]] = {s.get("id", ""): s for s in steps if s.get("id")}
        ids = [s.get("id", "") for s in steps if s.get("id")]

        win = tk.Toplevel(self.root)
        win.title("脚本流程图（只读）")
        win.geometry("1080x760")
        win.minsize(900, 620)

        wrapper = ttk.Frame(win, padding=8)
        wrapper.pack(fill="both", expand=True)

        canvas = tk.Canvas(wrapper, bg="#FFFFFF", highlightthickness=0)
        hbar = ttk.Scrollbar(wrapper, orient="horizontal", command=canvas.xview)
        vbar = ttk.Scrollbar(wrapper, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="we")
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)

        box_w = 180
        box_h = 72
        x_start = 120
        y_start = 90
        y_gap = 120

        pos: Dict[str, tuple[int, int]] = {}
        for i, sid in enumerate(ids):
            x = x_start
            y = y_start + i * y_gap
            pos[sid] = (x, y)
            step = step_map[sid]
            fill = "#E8F1FF" if step.get("type") == "IfElse" else "#F6F6F6"
            canvas.create_rectangle(x, y, x + box_w, y + box_h, outline="#5A5A5A", width=2, fill=fill)
            canvas.create_text(
                x + box_w // 2,
                y + box_h // 2,
                text=self._get_step_display_name(step),
                font=("Microsoft YaHei UI", 10),
            )

        start_id = self.flow.get("start") or (ids[0] if ids else "")
        if start_id in pos:
            sx, sy = pos[start_id]
            canvas.create_oval(sx - 58, sy + 18, sx - 18, sy + 58, fill="#D5F5D5", outline="#2E8B57", width=2)
            canvas.create_text(sx - 38, sy + 38, text="开始", font=("Microsoft YaHei UI", 9, "bold"), fill="#1F6B44")
            self._draw_arrow_with_label(canvas, sx - 18, sy + 38, sx, sy + 36, "start", color="#2E8B57")

        # STOP 节点
        stop_x = x_start + 420
        stop_y = y_start + max(0, len(ids) - 1) * y_gap
        canvas.create_oval(stop_x, stop_y, stop_x + 60, stop_y + 60, fill="#FFE0E0", outline="#B04040", width=2)
        canvas.create_text(stop_x + 30, stop_y + 30, text="STOP", font=("Microsoft YaHei UI", 10, "bold"), fill="#8A2020")

        for sid in ids:
            step = step_map[sid]
            x, y = pos[sid]
            stype = step.get("type", "")

            def get_target_center(tid: str) -> tuple[int, int]:
                if tid == "STOP":
                    return stop_x + 30, stop_y + 30
                if tid in pos:
                    tx, ty = pos[tid]
                    return tx + box_w // 2, ty + box_h // 2
                # 未定义目标，画到右侧提示区
                nx = x_start + 760
                ny = y
                canvas.create_text(nx, ny, text=f"未定义: {tid}", fill="#AA5500", anchor="w")
                return nx - 10, ny

            if stype == "IfElse":
                on_true = str(step.get("on_true", "STOP"))
                on_false = str(step.get("on_false", "STOP"))
                tx, ty = get_target_center(on_true)
                fx, fy = get_target_center(on_false)
                self._draw_arrow_with_label(canvas, x + box_w, y + 22, tx, ty, "true", color="#2E8B57")
                self._draw_arrow_with_label(canvas, x + box_w, y + 50, fx, fy, "false", color="#B06A00")
            else:
                nxt = str(step.get("next", "STOP"))
                nx, ny = get_target_center(nxt)
                self._draw_arrow_with_label(canvas, x + box_w, y + box_h // 2, nx, ny, "next", color="#3B4B8A")

            on_error = str(step.get("on_error", "STOP"))
            if on_error:
                ex, ey = get_target_center(on_error)
                self._draw_arrow_with_label(
                    canvas,
                    x + box_w // 2,
                    y + box_h,
                    ex,
                    ey,
                    "on_error",
                    color="#B04040",
                    dash=(4, 3),
                )

        canvas.create_text(
            x_start,
            30,
            anchor="w",
            text="说明：蓝=next，绿=true，橙=false，红虚线=on_error",
            font=("Microsoft YaHei UI", 9),
            fill="#444444",
        )
        canvas.configure(scrollregion=canvas.bbox("all"))

    def run(self) -> None:
        self.root.mainloop()

