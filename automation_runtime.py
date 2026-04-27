import ctypes
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from click_actions.image_click import ImageClickService
from click_actions.position_click import PositionClickService
from click_actions.text_click import TextClickService
from click_actions.text_type import TextTypeService


LogFn = Callable[[str], None]


@dataclass
class RunResult:
    ok: bool
    message: str
    step_id: Optional[str] = None


class RuntimeActions:
    def __init__(self, config_path: str = "set_ocr.txt", logger: Optional[LogFn] = None) -> None:
        self.logger = logger or (lambda _: None)
        self.text_service = TextClickService(config_path=config_path, exact_match=True, case_sensitive=False)
        self.position_service = PositionClickService()
        self.image_service = ImageClickService()
        self.text_type_service = TextTypeService()

    @staticmethod
    def _vk_from_key(key: str) -> int:
        if len(key) == 1:
            code = ctypes.windll.user32.VkKeyScanW(ord(key))
            return code & 0xFF
        named = {
            "enter": 0x0D,
            "tab": 0x09,
            "esc": 0x1B,
            "space": 0x20,
            "up": 0x26,
            "down": 0x28,
            "left": 0x25,
            "right": 0x27,
        }
        k = key.lower().strip()
        if k not in named:
            raise ValueError(f"不支持的按键: {key}")
        return named[k]

    @staticmethod
    def _key_press(vk: int) -> None:
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 0x0002, 0)

    def action_click_text(self, params: Dict[str, Any]) -> bool:
        target_text = str(params.get("text", "")).strip()
        delay = float(params.get("delay_sec", 0))
        exact = bool(params.get("exact_match", True))
        case_sensitive = bool(params.get("case_sensitive", False))
        click_times = int(params.get("click_times", 2 if bool(params.get("double_click", False)) else 1))
        click_interval = float(params.get("click_interval_sec", 0.08))
        self.text_service.exact_match = exact
        self.text_service.case_sensitive = case_sensitive
        res = self.text_service.click_text_once(
            target_text=target_text,
            delay_seconds=delay,
            click_times=click_times,
            click_interval_sec=click_interval,
        )
        self.logger(f"ClickText -> {res.to_json()}")
        return res.ok

    def action_click_position(self, params: Dict[str, Any]) -> bool:
        x = int(params.get("x"))
        y = int(params.get("y"))
        delay = float(params.get("delay_sec", 0))
        click_times = int(params.get("click_times", 2 if bool(params.get("double_click", False)) else 1))
        click_interval = float(params.get("click_interval_sec", 0.08))
        res = self.position_service.click_position_once(
            x=x,
            y=y,
            delay_seconds=delay,
            click_times=click_times,
            click_interval_sec=click_interval,
        )
        self.logger(f"ClickPosition -> ({res.x},{res.y})")
        return True

    def action_click_image(self, params: Dict[str, Any]) -> bool:
        template = str(params.get("template_path", "")).strip()
        confidence = float(params.get("confidence", 0.85))
        delay = float(params.get("delay_sec", 0))
        click_times = int(params.get("click_times", 2 if bool(params.get("double_click", False)) else 1))
        click_interval = float(params.get("click_interval_sec", 0.08))
        region = self.image_service.parse_region(params.get("region"))
        res = self.image_service.click_image_once(
            template_path=template,
            confidence=confidence,
            delay_seconds=delay,
            region=region,
            click_times=click_times,
            click_interval_sec=click_interval,
        )
        if not res.ok:
            self.logger("ClickImage -> 未匹配到模板")
            return False
        self.logger(f"ClickImage -> score={res.score:.3f}, center=({res.center_x},{res.center_y})")
        return True

    def action_type_text(self, params: Dict[str, Any]) -> bool:
        """在当前焦点处输入一段文字（Unicode，含中文）。请先让目标输入框获得焦点。"""
        content = str(params.get("content", "") or params.get("text", ""))
        if not content.strip() and not params.get("press_enter"):
            raise ValueError("TypeText 需要非空参数 content（或 text）")
        delay = float(params.get("delay_sec", 0))
        interval = float(params.get("interval_sec", 0))
        press_enter = bool(params.get("press_enter", False))
        self.text_type_service.type_text(
            content=content,
            delay_sec=delay,
            interval_sec=interval,
            press_enter=press_enter,
        )
        preview = content[:40] + ("..." if len(content) > 40 else "")
        self.logger(f"TypeText -> 已输入 {len(content)} 字, 预览={preview!r}, press_enter={press_enter}")
        return True

    def action_key_press(self, params: Dict[str, Any]) -> bool:
        key = str(params.get("key", "")).strip()
        delay = float(params.get("delay_sec", 0))
        times = int(params.get("times", 1))
        interval = float(params.get("interval_sec", 0.05))
        if delay > 0:
            time.sleep(delay)
        vk = self._vk_from_key(key)
        for _ in range(max(1, times)):
            self._key_press(vk)
            if interval > 0:
                time.sleep(interval)
        self.logger(f"KeyPress -> key={key}, times={times}")
        return True

    def action_wait(self, params: Dict[str, Any]) -> bool:
        sec = float(params.get("seconds", 1))
        time.sleep(max(0.0, sec))
        self.logger(f"Wait -> {sec}s")
        return True

    def cond_exists_text(self, params: Dict[str, Any]) -> bool:
        text = str(params.get("text", "")).strip()
        timeout = float(params.get("timeout_sec", 5))
        interval = float(params.get("interval_sec", 0.4))
        exact = bool(params.get("exact_match", True))
        case_sensitive = bool(params.get("case_sensitive", False))
        deadline = time.time() + max(0.1, timeout)
        while time.time() < deadline:
            matches = self.text_service.locator.locate_text_on_screen(
                target_text=text,
                region=None,
                exact_match=exact,
                case_sensitive=case_sensitive,
            )
            if matches:
                self.logger(f"ExistsText -> 命中 {len(matches)}")
                return True
            time.sleep(max(0.05, interval))
        self.logger("ExistsText -> 超时未命中")
        return False

    def cond_exists_image(self, params: Dict[str, Any]) -> bool:
        template = str(params.get("template_path", "")).strip()
        confidence = float(params.get("confidence", 0.85))
        timeout = float(params.get("timeout_sec", 5))
        interval = float(params.get("interval_sec", 0.4))
        region = self.image_service.parse_region(params.get("region"))
        deadline = time.time() + max(0.1, timeout)
        while time.time() < deadline:
            res = self.image_service.match_image(template_path=template, confidence=confidence, region=region)
            if res:
                self.logger(f"ExistsImage -> score={res.score:.3f}")
                return True
            time.sleep(max(0.05, interval))
        self.logger("ExistsImage -> 超时未命中")
        return False


class AutomationEngine:
    """
    流程格式:
    {
      "start": "step1",
      "steps": [
        {"id":"step1","type":"KeyPress","params":{"key":"1"},"next":"step2"},
        {"id":"step2","type":"IfElse","condition":{"type":"ExistsText","params":{"text":"成功"}},
         "on_true":"step3","on_false":"STOP"}
      ]
    }
    """

    def __init__(self, runtime: RuntimeActions, logger: Optional[LogFn] = None) -> None:
        self.runtime = runtime
        self.logger = logger or (lambda _: None)
        self._stop_requested = False
        self._actions = {
            "ClickText": self.runtime.action_click_text,
            "ClickPosition": self.runtime.action_click_position,
            "ClickImage": self.runtime.action_click_image,
            "TypeText": self.runtime.action_type_text,
            "KeyPress": self.runtime.action_key_press,
            "Wait": self.runtime.action_wait,
        }
        self._conditions = {
            "ExistsText": self.runtime.cond_exists_text,
            "ExistsImage": self.runtime.cond_exists_image,
        }

    def request_stop(self) -> None:
        self._stop_requested = True

    @staticmethod
    def load_flow(path: str) -> Dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def run_flow(self, flow: Dict[str, Any]) -> RunResult:
        self._stop_requested = False
        steps = flow.get("steps", [])
        step_map = {s["id"]: s for s in steps if "id" in s}
        current = flow.get("start") or (steps[0]["id"] if steps else None)
        if not current:
            return RunResult(ok=False, message="流程为空")
        while current and current != "STOP":
            if self._stop_requested:
                return RunResult(ok=False, message="已手动停止", step_id=current)
            if current not in step_map:
                return RunResult(ok=False, message=f"找不到步骤: {current}", step_id=current)
            step = step_map[current]
            sid = step["id"]
            stype = step.get("type", "")
            self.logger(f"[Step:{sid}] type={stype} 开始")
            try:
                if stype == "IfElse":
                    cond = step.get("condition", {})
                    ctype = cond.get("type")
                    cparams = cond.get("params", {})
                    if ctype not in self._conditions:
                        return RunResult(ok=False, message=f"不支持的条件类型: {ctype}", step_id=sid)
                    ok = self._conditions[ctype](cparams)
                    current = step.get("on_true") if ok else step.get("on_false")
                    self.logger(f"[Step:{sid}] 条件结果={ok} -> next={current}")
                    continue
                if stype not in self._actions:
                    return RunResult(ok=False, message=f"不支持的步骤类型: {stype}", step_id=sid)
                params = step.get("params", {})
                retry = int(step.get("retry_count", 0))
                retry_interval = float(step.get("retry_interval_sec", 0.3))
                success = False
                last_error = ""
                for i in range(retry + 1):
                    try:
                        success = self._actions[stype](params)
                    except Exception as err:
                        success = False
                        last_error = str(err)
                        self.logger(f"[Step:{sid}] 执行异常: {last_error}")
                    if success:
                        break
                    if i < retry:
                        self.logger(f"[Step:{sid}] 重试 {i+1}/{retry}")
                        time.sleep(max(0.0, retry_interval))
                if not success:
                    on_error = step.get("on_error", "STOP")
                    if on_error == "STOP":
                        msg = f"步骤失败: {sid}" + (f", err={last_error}" if last_error else "")
                        return RunResult(ok=False, message=msg, step_id=sid)
                    current = on_error
                    self.logger(f"[Step:{sid}] 失败后跳转 -> {current}")
                    continue
                post_delay = float(step.get("post_delay_sec", 0))
                if post_delay > 0:
                    time.sleep(post_delay)
                current = step.get("next", "STOP")
                self.logger(f"[Step:{sid}] 成功 -> next={current}")
            except Exception as e:
                return RunResult(ok=False, message=f"步骤异常: {sid}, {e}", step_id=sid)
        return RunResult(ok=True, message="流程执行完成", step_id=current)
