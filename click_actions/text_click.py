import ctypes
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from ocr.youdao_locator import TextMatch, YoudaoTextLocator


@dataclass
class ClickResult:
    ok: bool
    message: str
    target_text: str
    clicked_text: Optional[str] = None
    center: Optional[Dict[str, int]] = None
    ocr_center_raw: Optional[Dict[str, int]] = None
    bbox_image: Optional[Dict[str, int]] = None
    bbox_screen: Optional[Dict[str, int]] = None
    total_matches: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class TextClickService:
    def __init__(
        self,
        config_path: str = "set_ocr.txt",
        exact_match: bool = True,
        case_sensitive: bool = False,
    ) -> None:
        YoudaoTextLocator.enable_dpi_awareness()
        self.locator = YoudaoTextLocator(config_path=config_path)
        self.exact_match = exact_match
        self.case_sensitive = case_sensitive

    @staticmethod
    def _move_and_click(x: int, y: int, click_times: int = 1, click_interval_sec: float = 0.08) -> None:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        times = max(1, int(click_times))
        for i in range(times):
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            if i < times - 1 and click_interval_sec > 0:
                time.sleep(click_interval_sec)

    @staticmethod
    def _pick_best_match(matches: List[TextMatch]) -> TextMatch:
        def score_key(m: TextMatch) -> float:
            return m.score if m.score is not None else -1.0

        return sorted(matches, key=score_key, reverse=True)[0]

    def click_text_once(
        self,
        target_text: str,
        delay_seconds: float = 0.0,
        click_times: int = 1,
        click_interval_sec: float = 0.08,
    ) -> ClickResult:
        if not target_text or not target_text.strip():
            return ClickResult(
                ok=False,
                message="target_text 不能为空",
                target_text=target_text,
                timestamp=time.time(),
            )

        if delay_seconds > 0:
            time.sleep(delay_seconds)

        matches = self.locator.locate_text_on_screen(
            target_text=target_text,
            region=None,
            exact_match=self.exact_match,
            case_sensitive=self.case_sensitive,
        )
        if not matches:
            return ClickResult(
                ok=False,
                message=f"未找到目标文字: {target_text}",
                target_text=target_text,
                total_matches=0,
                timestamp=time.time(),
            )

        best = self._pick_best_match(matches)
        click_x = best.center_x_screen if best.center_x_screen is not None else best.center_x
        click_y = best.center_y_screen if best.center_y_screen is not None else best.center_y
        self._move_and_click(click_x, click_y, click_times=click_times, click_interval_sec=click_interval_sec)

        return ClickResult(
            ok=True,
            message="点击成功",
            target_text=target_text,
            clicked_text=best.text,
            center={"x": click_x, "y": click_y},
            ocr_center_raw={"x": best.center_x, "y": best.center_y},
            bbox_image={"left": best.left, "top": best.top, "right": best.right, "bottom": best.bottom},
            bbox_screen={
                "left": best.left_screen if best.left_screen is not None else best.left,
                "top": best.top_screen if best.top_screen is not None else best.top,
                "right": best.right_screen if best.right_screen is not None else best.right,
                "bottom": best.bottom_screen if best.bottom_screen is not None else best.bottom,
            },
            total_matches=len(matches),
            timestamp=time.time(),
        )
