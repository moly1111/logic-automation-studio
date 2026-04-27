import threading
import tkinter as tk
from tkinter import ttk

from click_actions.text_click import TextClickService
from ocr.youdao_locator import YoudaoTextLocator


class ClickControlPanel:
    def __init__(self) -> None:
        # 先启用 DPI 感知，再创建 Tk 窗口，避免运行时窗口缩放变化。
        YoudaoTextLocator.enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.title("文字点击控制面板")
        self.root.geometry("520x320")
        self.root.resizable(False, False)

        self.running = False
        self.worker: threading.Thread | None = None
        self.service = TextClickService(config_path="set_ocr.txt", exact_match=True, case_sensitive=False)

        self.target_text_var = tk.StringVar(value="发送")
        self.delay_var = tk.StringVar(value="0")
        self.exact_var = tk.BooleanVar(value=True)
        self.case_var = tk.BooleanVar(value=False)
        self.double_click_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="未开始")

        self._build_ui()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="需要点击的文字:").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.target_text_var, width=36).grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(frame, text="延迟秒数:").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.delay_var, width=12).grid(row=1, column=1, sticky="w", pady=6)

        ttk.Checkbutton(frame, text="精确匹配", variable=self.exact_var).grid(row=2, column=0, sticky="w", pady=6)
        ttk.Checkbutton(frame, text="区分大小写", variable=self.case_var).grid(row=2, column=1, sticky="w", pady=6)
        ttk.Checkbutton(frame, text="双击", variable=self.double_click_var).grid(row=3, column=0, sticky="w", pady=6)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Button(btn_frame, text="开始", command=self.start).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="停止", command=self.stop).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="立即执行一次", command=self.run_once_async).pack(side="left")

        ttk.Label(frame, text="运行状态:").grid(row=5, column=0, sticky="nw", pady=(12, 4))
        ttk.Label(frame, textvariable=self.status_var, wraplength=380, justify="left").grid(
            row=5, column=1, sticky="w", pady=(12, 4)
        )

        tips = (
            "说明: 点击“开始”后会按当前设置执行一次（可延迟），完成后自动停止。"
            "如需再次执行，继续点击“开始”或“立即执行一次”。"
        )
        ttk.Label(frame, text=tips, wraplength=480, justify="left").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(14, 0)
        )

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _hide_window(self) -> None:
        self.root.withdraw()

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _read_delay(self) -> float:
        try:
            delay = float(self.delay_var.get().strip() or "0")
            if delay < 0:
                return 0.0
            return delay
        except ValueError:
            return 0.0

    def _build_service(self) -> None:
        self.service = TextClickService(
            config_path="set_ocr.txt",
            exact_match=self.exact_var.get(),
            case_sensitive=self.case_var.get(),
        )

    def _run_once(self) -> None:
        self._build_service()
        target_text = self.target_text_var.get().strip()
        delay = self._read_delay()
        self._set_status(f"执行中: 目标={target_text}, 延迟={delay}s")
        result = self.service.click_text_once(
            target_text=target_text,
            delay_seconds=delay,
            click_times=2 if self.double_click_var.get() else 1,
        )
        if result.ok:
            self._set_status(
                f"成功: 点击[{result.clicked_text}] at ({result.center['x']},{result.center['y']}), "
                f"命中数={result.total_matches}"
            )
        else:
            self._set_status(f"失败: {result.message}")
        self.running = False
        self.root.after(0, self._show_window)

    def _run_once_bg(self) -> None:
        try:
            self._run_once()
        except Exception as exc:
            self._set_status(f"异常: {exc}")
            self.running = False
            self.root.after(0, self._show_window)

    def run_once_async(self) -> None:
        if self.running:
            self._set_status("已有任务在执行中")
            return
        self.running = True
        self.worker = threading.Thread(target=self._run_once_bg, daemon=True)
        self.worker.start()

    def start(self) -> None:
        self._hide_window()
        self.run_once_async()

    def stop(self) -> None:
        # OCR 请求无法立即硬中断，这里仅阻止新任务并更新状态。
        self.running = False
        self._set_status("已停止（当前任务若已发起，会在结束后停止）")

    def run(self) -> None:
        self.root.mainloop()

