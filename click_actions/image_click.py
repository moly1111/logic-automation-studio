import ctypes
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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


@dataclass
class ImageClickResult:
    ok: bool
    message: str
    score: Optional[float] = None
    center_x: Optional[int] = None
    center_y: Optional[int] = None
    left: Optional[int] = None
    top: Optional[int] = None
    right: Optional[int] = None
    bottom: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class ImageClickService:
    @staticmethod
    def get_virtual_screen_region() -> Tuple[int, int, int, int]:
        """多显示器下与主屏无关的虚拟桌面矩形，用于全屏截图与坐标换算。"""
        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
        top = int(user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
        width = int(user32.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
        height = int(user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
        return left, top, left + width, top + height

    @staticmethod
    def _click(x: int, y: int, click_times: int = 1, click_interval_sec: float = 0.08) -> None:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        times = max(1, int(click_times))
        for i in range(times):
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            if i < times - 1 and click_interval_sec > 0:
                time.sleep(click_interval_sec)

    @staticmethod
    def _grab(region: Optional[Tuple[int, int, int, int]] = None):
        if ImageGrab is None:
            raise RuntimeError("未安装 Pillow，无法截图")
        return ImageGrab.grab(bbox=region)

    @staticmethod
    def parse_region(region: Any) -> Optional[Tuple[int, int, int, int]]:
        """
        None / 空 / auto / full 表示不限制区域，运行时用虚拟屏幕全区域（自适应分辨率）。
        """
        if region is None or region == "" or region == []:
            return None
        if isinstance(region, str):
            s = region.strip().lower()
            if s in ("", "auto", "full", "fullscreen", "all", "*"):
                return None
        if isinstance(region, (list, tuple)) and len(region) == 4:
            return int(region[0]), int(region[1]), int(region[2]), int(region[3])
        raise ValueError("region 必须是 [left,top,right,bottom] 或留空/auto 表示全屏自适配")

    def match_image(
        self,
        template_path: str,
        confidence: float = 0.85,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[ImageClickResult]:
        if cv2 is None or np is None:
            raise RuntimeError("未安装 opencv-python 和 numpy，无法使用图片识别")
        p = Path(template_path)
        if not p.exists():
            raise FileNotFoundError(f"模板图片不存在: {template_path}")
        capture = region if region is not None else self.get_virtual_screen_region()
        screenshot = self._grab(region=capture)
        screen_bgr = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        # Windows 下中文路径可能导致 cv2.imread 失败，改用 imdecode 兼容 Unicode 路径。
        raw = np.fromfile(str(p), dtype=np.uint8)
        tpl_bgr = cv2.imdecode(raw, cv2.IMREAD_COLOR)
        if tpl_bgr is None:
            raise RuntimeError(f"模板图片读取失败: {template_path}")
        res = cv2.matchTemplate(screen_bgr, tpl_bgr, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if float(max_val) < confidence:
            return None
        x, y = max_loc
        h, w = tpl_bgr.shape[:2]
        left = x + capture[0]
        top = y + capture[1]
        right = left + w
        bottom = top + h
        return ImageClickResult(
            ok=True,
            message="匹配成功",
            score=float(max_val),
            center_x=int((left + right) / 2),
            center_y=int((top + bottom) / 2),
            left=int(left),
            top=int(top),
            right=int(right),
            bottom=int(bottom),
        )

    def click_image_once(
        self,
        template_path: str,
        confidence: float = 0.85,
        delay_seconds: float = 0.0,
        region: Optional[Tuple[int, int, int, int]] = None,
        click_times: int = 1,
        click_interval_sec: float = 0.08,
    ) -> ImageClickResult:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        result = self.match_image(template_path=template_path, confidence=confidence, region=region)
        if not result:
            return ImageClickResult(ok=False, message="未匹配到模板")
        self._click(
            result.center_x,  # type: ignore[arg-type]
            result.center_y,  # type: ignore[arg-type]
            click_times=click_times,
            click_interval_sec=click_interval_sec,
        )
        return result
