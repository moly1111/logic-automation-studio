import ctypes
import json
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from click_actions.image_click import ImageClickService
from click_actions.position_click import PositionClickService
from click_actions.text_click import TextClickService
from click_actions.text_type import TextTypeService

try:
    from PIL import ImageGrab
except Exception:  # pragma: no cover
    ImageGrab = None

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None


LogFn = Callable[[str], None]


@dataclass
class RunResult:
    ok: bool
    message: str
    step_id: Optional[str] = None


@dataclass
class LazyModeConfig:
    enabled: bool = False
    grid_rows: int = 10
    grid_cols: int = 10
    sample_blocks: int = 9
    similarity_threshold: float = 0.88
    recheck_interval: int = 50
    warmup_repeat_threshold: float = 0.9
    warmup_coord_tolerance_px: int = 5


@dataclass
class StepReference:
    click_x: Optional[int]
    click_y: Optional[int]
    before_blocks: List[Any]
    after_blocks: List[Any]


class RuntimeActions:
    def __init__(
        self,
        config_path: str = "set_ocr.txt",
        logger: Optional[LogFn] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self.logger = logger or (lambda _: None)
        self.stop_event = stop_event
        self.text_service = TextClickService(config_path=config_path, exact_match=True, case_sensitive=False)
        self.position_service = PositionClickService()
        self.image_service = ImageClickService()
        self.text_type_service = TextTypeService()
        self.lazy_cfg = LazyModeConfig()
        self.execution_mode = "STRICT"  # STRICT / LAZY
        self.guard_step_id: Optional[str] = None
        self.alert_requested = False
        self.references: Dict[str, StepReference] = {}
        self.reference_versions: Dict[str, int] = {}
        self.guard_check_index = 0
        self.guard_debug_root = Path("save") / "guard_debug" / datetime.now().strftime("%Y%m%d_%H%M%S")
        self._round_cacheable_total = 0
        self._round_cacheable_compared = 0
        self._round_cacheable_stable = 0
        (self.guard_debug_root / "reference").mkdir(parents=True, exist_ok=True)
        (self.guard_debug_root / "checks").mkdir(parents=True, exist_ok=True)

    def _is_stopped(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set())

    def _sleep_interruptible(self, seconds: float, step: float = 0.05) -> bool:
        end_at = time.time() + max(0.0, seconds)
        while time.time() < end_at:
            if self._is_stopped():
                return False
            time.sleep(min(step, end_at - time.time()))
        return True

    def configure_lazy_mode(self, cfg: LazyModeConfig) -> None:
        self.lazy_cfg = cfg

    def set_execution_mode(self, mode: str, guard_step_id: Optional[str] = None) -> None:
        self.execution_mode = mode
        self.guard_step_id = guard_step_id
        self.alert_requested = False
        self._round_cacheable_total = 0
        self._round_cacheable_compared = 0
        self._round_cacheable_stable = 0

    def get_cached_action_step_ids(self) -> List[str]:
        return list(self.references.keys())

    def consume_alert_requested(self) -> bool:
        flag = self.alert_requested
        self.alert_requested = False
        return flag

    def get_warmup_repeatability(self) -> Dict[str, Any]:
        if self._round_cacheable_compared <= 0:
            ratio = 0.0
        else:
            ratio = float(self._round_cacheable_stable) / float(self._round_cacheable_compared)
        return {
            "cacheable_total": self._round_cacheable_total,
            "compared": self._round_cacheable_compared,
            "stable": self._round_cacheable_stable,
            "repeat_ratio": ratio,
            "threshold": float(self.lazy_cfg.warmup_repeat_threshold),
            "coord_tolerance_px": int(self.lazy_cfg.warmup_coord_tolerance_px),
            "qualified": (
                self._round_cacheable_total > 0
                and self._round_cacheable_compared == self._round_cacheable_total
                and ratio >= float(self.lazy_cfg.warmup_repeat_threshold)
            ),
        }

    @staticmethod
    def _grab_screen() -> Any:
        if ImageGrab is None:
            raise RuntimeError("未安装 Pillow，无法截图用于慵懒守卫")
        return ImageGrab.grab()

    def _image_to_blocks(self, img: Any) -> List[Any]:
        if cv2 is None or np is None:
            raise RuntimeError("未安装 opencv-python/numpy，无法执行慵懒守卫")
        arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
        h, w = arr.shape[:2]
        rows = max(1, int(self.lazy_cfg.grid_rows))
        cols = max(1, int(self.lazy_cfg.grid_cols))
        bh = max(1, h // rows)
        bw = max(1, w // cols)
        blocks: List[Any] = []
        for r in range(rows):
            for c in range(cols):
                y1 = r * bh
                x1 = c * bw
                y2 = h if r == rows - 1 else (r + 1) * bh
                x2 = w if c == cols - 1 else (c + 1) * bw
                blocks.append(arr[y1:y2, x1:x2])
        return blocks

    @staticmethod
    def _block_similarity(a: Any, b: Any) -> float:
        if cv2 is None:
            return 0.0
        if a is None or b is None or a.size == 0 or b.size == 0:
            return 0.0
        if a.shape != b.shape:
            b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
        score = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0][0]
        return float(score)

    def _vote_guard(
        self, ref: StepReference, before_now: List[Any], after_now: List[Any]
    ) -> Tuple[bool, List[int], List[Dict[str, Any]], int, int]:
        total = min(len(ref.before_blocks), len(before_now), len(ref.after_blocks), len(after_now))
        if total <= 0:
            return False
        k = max(1, int(self.lazy_cfg.sample_blocks))
        if k % 2 == 0:
            k += 1
        k = min(k, total if total % 2 == 1 else total - 1 if total > 1 else 1)
        idxs = random.sample(list(range(total)), k=k)
        agrees = 0
        details: List[Dict[str, Any]] = []
        for i in idxs:
            s1 = self._block_similarity(ref.before_blocks[i], before_now[i])
            s2 = self._block_similarity(ref.after_blocks[i], after_now[i])
            vote = (s1 >= self.lazy_cfg.similarity_threshold) and (s2 >= self.lazy_cfg.similarity_threshold)
            if vote:
                agrees += 1
            details.append(
                {
                    "block_index": i,
                    "before_similarity": float(s1),
                    "after_similarity": float(s2),
                    "vote": bool(vote),
                }
            )
        passed = agrees > (k / 2.0)
        self.logger(
            f"LazyGuard -> sampled={k}, agree={agrees}, pass={passed}, threshold={self.lazy_cfg.similarity_threshold}"
        )
        return passed, idxs, details, k, agrees

    @staticmethod
    def _safe_step_name(step_id: str) -> str:
        clean = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (step_id or "unknown"))
        return clean or "unknown"

    @staticmethod
    def _save_block_png(block: Any, target_path: Path) -> None:
        if cv2 is None:
            return
        target_path.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".png", block)
        if ok:
            buf.tofile(str(target_path))

    def _save_reference_blocks(self, step_id: str, ref: StepReference) -> None:
        safe_step = self._safe_step_name(step_id)
        ver = self.reference_versions.get(step_id, 0) + 1
        self.reference_versions[step_id] = ver
        ref_dir = self.guard_debug_root / "reference" / safe_step / f"capture_{ver:04d}"
        ref_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "step_id": step_id,
            "capture_version": ver,
            "click_x": ref.click_x,
            "click_y": ref.click_y,
            "block_count": len(ref.before_blocks),
        }
        (ref_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        for i, blk in enumerate(ref.before_blocks):
            self._save_block_png(blk, ref_dir / "before" / f"block_{i:04d}.png")
        for i, blk in enumerate(ref.after_blocks):
            self._save_block_png(blk, ref_dir / "after" / f"block_{i:04d}.png")

    def _save_guard_check(
        self,
        step_id: str,
        sampled_idxs: List[int],
        details: List[Dict[str, Any]],
        k: int,
        agrees: int,
        passed: bool,
        ref: StepReference,
        before_now: List[Any],
        after_now: List[Any],
    ) -> None:
        self.guard_check_index += 1
        safe_step = self._safe_step_name(step_id)
        chk_dir = self.guard_debug_root / "checks" / f"check_{self.guard_check_index:06d}" / safe_step
        chk_dir.mkdir(parents=True, exist_ok=True)

        for idx in sampled_idxs:
            self._save_block_png(before_now[idx], chk_dir / "realtime_before" / f"block_{idx:04d}.png")
            self._save_block_png(after_now[idx], chk_dir / "realtime_after" / f"block_{idx:04d}.png")

        ref_version = self.reference_versions.get(step_id, 1)
        ref_base = self.guard_debug_root / "reference" / self._safe_step_name(step_id) / f"capture_{ref_version:04d}"
        report = {
            "check_index": self.guard_check_index,
            "step_id": step_id,
            "sampled_blocks": sampled_idxs,
            "sampled_count": k,
            "agree_count": agrees,
            "passed": passed,
            "threshold": float(self.lazy_cfg.similarity_threshold),
            "reference_path": str(ref_base),
            "details": details,
        }
        (chk_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [
            f"check_index={self.guard_check_index}",
            f"step_id={step_id}",
            f"sampled_blocks={sampled_idxs}",
            f"sampled_count={k}, agree_count={agrees}, passed={passed}",
            f"threshold={self.lazy_cfg.similarity_threshold}",
            f"reference_path={ref_base}",
            "",
            "per_block_details:",
        ]
        for d in details:
            lines.append(
                f"  block={d['block_index']}, before_sim={d['before_similarity']:.4f}, "
                f"after_sim={d['after_similarity']:.4f}, vote={d['vote']}"
            )
        (chk_dir / "report.txt").write_text("\n".join(lines), encoding="utf-8")
        self.logger(f"LazyGuard 落盘 -> check={self.guard_check_index}, step={step_id}, dir={chk_dir}")

    def _capture_reference(self, step_id: str, click_x: int, click_y: int, before_img: Any, after_img: Any) -> None:
        before_blocks = self._image_to_blocks(before_img)
        after_blocks = self._image_to_blocks(after_img)
        ref = StepReference(
            click_x=click_x,
            click_y=click_y,
            before_blocks=before_blocks,
            after_blocks=after_blocks,
        )
        self.references[step_id] = ref
        self._save_reference_blocks(step_id, ref)

    def _record_step_reference(
        self,
        step_id: str,
        before_img: Any,
        after_img: Any,
        click_x: Optional[int] = None,
        click_y: Optional[int] = None,
    ) -> None:
        new_before = self._image_to_blocks(before_img)
        new_after = self._image_to_blocks(after_img)
        old_ref = self.references.get(step_id)
        # 参考块固定为该步骤首次严格成功时的基线，不在后续轮次覆盖。
        if old_ref is None:
            new_ref = StepReference(
                click_x=click_x,
                click_y=click_y,
                before_blocks=new_before,
                after_blocks=new_after,
            )
            self.references[step_id] = new_ref
            self._save_reference_blocks(step_id, new_ref)
            return
        if self.execution_mode == "STRICT":
            self._round_cacheable_compared += 1
            if self._is_reference_stable(old_ref, click_x, click_y, new_before, new_after):
                self._round_cacheable_stable += 1

    def _is_reference_stable(
        self,
        old_ref: StepReference,
        new_click_x: Optional[int],
        new_click_y: Optional[int],
        new_before_blocks: List[Any],
        new_after_blocks: List[Any],
    ) -> bool:
        if (
            old_ref.click_x is not None
            and old_ref.click_y is not None
            and new_click_x is not None
            and new_click_y is not None
        ):
            dx = abs(int(old_ref.click_x) - int(new_click_x))
            dy = abs(int(old_ref.click_y) - int(new_click_y))
            if dx > int(self.lazy_cfg.warmup_coord_tolerance_px) or dy > int(self.lazy_cfg.warmup_coord_tolerance_px):
                return False
        passed, _, _, _, _ = self._vote_guard(
            old_ref,
            before_now=new_before_blocks,
            after_now=new_after_blocks,
        )
        return passed

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
        if self._is_stopped():
            return False
        step_id = str(params.get("_step_id", "")).strip()
        target_text = str(params.get("text", "")).strip()
        delay = float(params.get("delay_sec", 0))
        exact = bool(params.get("exact_match", True))
        case_sensitive = bool(params.get("case_sensitive", False))
        click_times = int(params.get("click_times", 2 if bool(params.get("double_click", False)) else 1))
        click_interval = float(params.get("click_interval_sec", 0.08))
        self.text_service.exact_match = exact
        self.text_service.case_sensitive = case_sensitive
        # Lazy: 尝试复用缓存坐标，仅守卫步做低耗校验
        if self.execution_mode == "LAZY" and step_id in self.references:
            ref = self.references[step_id]
            before_img = self._grab_screen() if self.lazy_cfg.enabled else None
            self.position_service.click_position_once(
                x=ref.click_x,
                y=ref.click_y,
                delay_seconds=delay,
                click_times=click_times,
                click_interval_sec=click_interval,
            )
            if self.guard_step_id == step_id and self.lazy_cfg.enabled:
                after_img = self._grab_screen()
                before_now = self._image_to_blocks(before_img)
                after_now = self._image_to_blocks(after_img)
                passed, sampled_idxs, details, k, agrees = self._vote_guard(ref, before_now, after_now)
                self._save_guard_check(
                    step_id=step_id,
                    sampled_idxs=sampled_idxs,
                    details=details,
                    k=k,
                    agrees=agrees,
                    passed=passed,
                    ref=ref,
                    before_now=before_now,
                    after_now=after_now,
                )
                if not passed:
                    self.alert_requested = True
            self.logger(f"ClickText(LAZY) -> ({ref.click_x},{ref.click_y})")
            return True

        if self.lazy_cfg.enabled and self.execution_mode == "STRICT":
            self._round_cacheable_total += 1
        before_img = self._grab_screen() if self.lazy_cfg.enabled else None
        res = self.text_service.click_text_once(
            target_text=target_text,
            delay_seconds=delay,
            click_times=click_times,
            click_interval_sec=click_interval,
        )
        after_img = self._grab_screen() if self.lazy_cfg.enabled else None
        if res.ok and self.lazy_cfg.enabled and step_id and before_img is not None and after_img is not None and res.center:
            self._record_step_reference(
                step_id=step_id,
                before_img=before_img,
                after_img=after_img,
                click_x=int(res.center["x"]),
                click_y=int(res.center["y"]),
            )
        self.logger(f"ClickText -> {res.to_json()}")
        return res.ok

    def action_click_position(self, params: Dict[str, Any]) -> bool:
        if self._is_stopped():
            return False
        step_id = str(params.get("_step_id", "")).strip()
        x = int(params.get("x"))
        y = int(params.get("y"))
        delay = float(params.get("delay_sec", 0))
        click_times = int(params.get("click_times", 2 if bool(params.get("double_click", False)) else 1))
        click_interval = float(params.get("click_interval_sec", 0.08))
        if self.lazy_cfg.enabled and self.execution_mode == "STRICT":
            self._round_cacheable_total += 1
        before_img = self._grab_screen() if self.lazy_cfg.enabled else None
        res = self.position_service.click_position_once(
            x=x,
            y=y,
            delay_seconds=delay,
            click_times=click_times,
            click_interval_sec=click_interval,
        )
        after_img = self._grab_screen() if self.lazy_cfg.enabled else None
        if self.lazy_cfg.enabled and step_id and before_img is not None and after_img is not None:
            self._record_step_reference(
                step_id=step_id,
                before_img=before_img,
                after_img=after_img,
                click_x=int(res.x),
                click_y=int(res.y),
            )
            if self.execution_mode == "LAZY" and self.guard_step_id == step_id:
                ref = self.references.get(step_id)
                if ref is not None:
                    before_now = self._image_to_blocks(before_img)
                    after_now = self._image_to_blocks(after_img)
                    passed, sampled_idxs, details, k, agrees = self._vote_guard(ref, before_now, after_now)
                    self._save_guard_check(
                        step_id=step_id,
                        sampled_idxs=sampled_idxs,
                        details=details,
                        k=k,
                        agrees=agrees,
                        passed=passed,
                        ref=ref,
                        before_now=before_now,
                        after_now=after_now,
                    )
                    if not passed:
                        self.alert_requested = True
        self.logger(f"ClickPosition -> ({res.x},{res.y})")
        return True

    def action_click_image(self, params: Dict[str, Any]) -> bool:
        if self._is_stopped():
            return False
        step_id = str(params.get("_step_id", "")).strip()
        template = str(params.get("template_path", "")).strip()
        confidence = float(params.get("confidence", 0.85))
        delay = float(params.get("delay_sec", 0))
        click_times = int(params.get("click_times", 2 if bool(params.get("double_click", False)) else 1))
        click_interval = float(params.get("click_interval_sec", 0.08))
        region = self.image_service.parse_region(params.get("region"))
        if self.execution_mode == "LAZY" and step_id in self.references:
            ref = self.references[step_id]
            before_img = self._grab_screen() if self.lazy_cfg.enabled else None
            self.position_service.click_position_once(
                x=ref.click_x,
                y=ref.click_y,
                delay_seconds=delay,
                click_times=click_times,
                click_interval_sec=click_interval,
            )
            if self.guard_step_id == step_id and self.lazy_cfg.enabled:
                after_img = self._grab_screen()
                before_now = self._image_to_blocks(before_img)
                after_now = self._image_to_blocks(after_img)
                passed, sampled_idxs, details, k, agrees = self._vote_guard(ref, before_now, after_now)
                self._save_guard_check(
                    step_id=step_id,
                    sampled_idxs=sampled_idxs,
                    details=details,
                    k=k,
                    agrees=agrees,
                    passed=passed,
                    ref=ref,
                    before_now=before_now,
                    after_now=after_now,
                )
                if not passed:
                    self.alert_requested = True
            self.logger(f"ClickImage(LAZY) -> ({ref.click_x},{ref.click_y})")
            return True

        if self.lazy_cfg.enabled and self.execution_mode == "STRICT":
            self._round_cacheable_total += 1
        before_img = self._grab_screen() if self.lazy_cfg.enabled else None
        res = self.image_service.click_image_once(
            template_path=template,
            confidence=confidence,
            delay_seconds=delay,
            region=region,
            click_times=click_times,
            click_interval_sec=click_interval,
        )
        after_img = self._grab_screen() if self.lazy_cfg.enabled else None
        if not res.ok:
            self.logger("ClickImage -> 未匹配到模板")
            return False
        if (
            self.lazy_cfg.enabled
            and step_id
            and before_img is not None
            and after_img is not None
            and res.center_x is not None
            and res.center_y is not None
        ):
            self._record_step_reference(
                step_id=step_id,
                before_img=before_img,
                after_img=after_img,
                click_x=int(res.center_x),
                click_y=int(res.center_y),
            )
        self.logger(f"ClickImage -> score={res.score:.3f}, center=({res.center_x},{res.center_y})")
        return True

    def action_type_text(self, params: Dict[str, Any]) -> bool:
        if self._is_stopped():
            return False
        step_id = str(params.get("_step_id", "")).strip()
        """在当前焦点处输入一段文字（Unicode，含中文）。请先让目标输入框获得焦点。"""
        content = str(params.get("content", "") or params.get("text", ""))
        if not content.strip() and not params.get("press_enter"):
            raise ValueError("TypeText 需要非空参数 content（或 text）")
        delay = float(params.get("delay_sec", 0))
        interval = float(params.get("interval_sec", 0))
        press_enter = bool(params.get("press_enter", False))
        if self.lazy_cfg.enabled and self.execution_mode == "STRICT":
            self._round_cacheable_total += 1
        before_img = self._grab_screen() if self.lazy_cfg.enabled else None
        self.text_type_service.type_text(
            content=content,
            delay_sec=delay,
            interval_sec=interval,
            press_enter=press_enter,
        )
        after_img = self._grab_screen() if self.lazy_cfg.enabled else None
        if self.lazy_cfg.enabled and step_id and before_img is not None and after_img is not None:
            self._record_step_reference(step_id=step_id, before_img=before_img, after_img=after_img)
            if self.execution_mode == "LAZY" and self.guard_step_id == step_id:
                ref = self.references.get(step_id)
                if ref is not None:
                    before_now = self._image_to_blocks(before_img)
                    after_now = self._image_to_blocks(after_img)
                    passed, sampled_idxs, details, k, agrees = self._vote_guard(ref, before_now, after_now)
                    self._save_guard_check(
                        step_id=step_id,
                        sampled_idxs=sampled_idxs,
                        details=details,
                        k=k,
                        agrees=agrees,
                        passed=passed,
                        ref=ref,
                        before_now=before_now,
                        after_now=after_now,
                    )
                    if not passed:
                        self.alert_requested = True
        preview = content[:40] + ("..." if len(content) > 40 else "")
        self.logger(f"TypeText -> 已输入 {len(content)} 字, 预览={preview!r}, press_enter={press_enter}")
        return True

    def action_key_press(self, params: Dict[str, Any]) -> bool:
        if self._is_stopped():
            return False
        step_id = str(params.get("_step_id", "")).strip()
        key = str(params.get("key", "")).strip()
        delay = float(params.get("delay_sec", 0))
        times = int(params.get("times", 1))
        interval = float(params.get("interval_sec", 0.05))
        if delay > 0 and not self._sleep_interruptible(delay):
            return False
        if self.lazy_cfg.enabled and self.execution_mode == "STRICT":
            self._round_cacheable_total += 1
        before_img = self._grab_screen() if self.lazy_cfg.enabled else None
        vk = self._vk_from_key(key)
        for _ in range(max(1, times)):
            if self._is_stopped():
                return False
            self._key_press(vk)
            if interval > 0 and not self._sleep_interruptible(interval):
                return False
        after_img = self._grab_screen() if self.lazy_cfg.enabled else None
        if self.lazy_cfg.enabled and step_id and before_img is not None and after_img is not None:
            self._record_step_reference(step_id=step_id, before_img=before_img, after_img=after_img)
            if self.execution_mode == "LAZY" and self.guard_step_id == step_id:
                ref = self.references.get(step_id)
                if ref is not None:
                    before_now = self._image_to_blocks(before_img)
                    after_now = self._image_to_blocks(after_img)
                    passed, sampled_idxs, details, k, agrees = self._vote_guard(ref, before_now, after_now)
                    self._save_guard_check(
                        step_id=step_id,
                        sampled_idxs=sampled_idxs,
                        details=details,
                        k=k,
                        agrees=agrees,
                        passed=passed,
                        ref=ref,
                        before_now=before_now,
                        after_now=after_now,
                    )
                    if not passed:
                        self.alert_requested = True
        self.logger(f"KeyPress -> key={key}, times={times}")
        return True

    def action_wait(self, params: Dict[str, Any]) -> bool:
        step_id = str(params.get("_step_id", "")).strip()
        sec = float(params.get("seconds", 1))
        if self.lazy_cfg.enabled and self.execution_mode == "STRICT":
            self._round_cacheable_total += 1
        before_img = self._grab_screen() if self.lazy_cfg.enabled else None
        if not self._sleep_interruptible(sec):
            return False
        after_img = self._grab_screen() if self.lazy_cfg.enabled else None
        if self.lazy_cfg.enabled and step_id and before_img is not None and after_img is not None:
            self._record_step_reference(step_id=step_id, before_img=before_img, after_img=after_img)
            if self.execution_mode == "LAZY" and self.guard_step_id == step_id:
                ref = self.references.get(step_id)
                if ref is not None:
                    before_now = self._image_to_blocks(before_img)
                    after_now = self._image_to_blocks(after_img)
                    passed, sampled_idxs, details, k, agrees = self._vote_guard(ref, before_now, after_now)
                    self._save_guard_check(
                        step_id=step_id,
                        sampled_idxs=sampled_idxs,
                        details=details,
                        k=k,
                        agrees=agrees,
                        passed=passed,
                        ref=ref,
                        before_now=before_now,
                        after_now=after_now,
                    )
                    if not passed:
                        self.alert_requested = True
        self.logger(f"Wait -> {sec}s")
        return True

    def cond_exists_text(self, params: Dict[str, Any]) -> bool:
        if self._is_stopped():
            return False
        text = str(params.get("text", "")).strip()
        timeout = float(params.get("timeout_sec", 5))
        interval = float(params.get("interval_sec", 0.4))
        exact = bool(params.get("exact_match", True))
        case_sensitive = bool(params.get("case_sensitive", False))
        deadline = time.time() + max(0.1, timeout)
        while time.time() < deadline:
            if self._is_stopped():
                return False
            matches = self.text_service.locator.locate_text_on_screen(
                target_text=text,
                region=None,
                exact_match=exact,
                case_sensitive=case_sensitive,
            )
            if matches:
                self.logger(f"ExistsText -> 命中 {len(matches)}")
                return True
            if not self._sleep_interruptible(max(0.05, interval)):
                return False
        self.logger("ExistsText -> 超时未命中")
        return False

    def cond_exists_image(self, params: Dict[str, Any]) -> bool:
        if self._is_stopped():
            return False
        template = str(params.get("template_path", "")).strip()
        confidence = float(params.get("confidence", 0.85))
        timeout = float(params.get("timeout_sec", 5))
        interval = float(params.get("interval_sec", 0.4))
        region = self.image_service.parse_region(params.get("region"))
        deadline = time.time() + max(0.1, timeout)
        while time.time() < deadline:
            if self._is_stopped():
                return False
            res = self.image_service.match_image(template_path=template, confidence=confidence, region=region)
            if res:
                self.logger(f"ExistsImage -> score={res.score:.3f}")
                return True
            if not self._sleep_interruptible(max(0.05, interval)):
                return False
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
                params = dict(step.get("params", {}))
                params["_step_id"] = sid
                retry = int(step.get("retry_count", 0))
                retry_interval = float(step.get("retry_interval_sec", 0.3))
                success = False
                last_error = ""
                for i in range(retry + 1):
                    if self._stop_requested:
                        return RunResult(ok=False, message="已手动停止", step_id=sid)
                    try:
                        success = self._actions[stype](params)
                    except Exception as err:
                        success = False
                        last_error = str(err)
                        self.logger(f"[Step:{sid}] 执行异常: {last_error}")
                    if success:
                        break
                    if i < retry:
                        if self._stop_requested:
                            return RunResult(ok=False, message="已手动停止", step_id=sid)
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
