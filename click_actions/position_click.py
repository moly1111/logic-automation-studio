import ctypes
import time
from dataclasses import asdict, dataclass
from typing import Dict


@dataclass
class PositionClickResult:
    ok: bool
    x: int
    y: int
    message: str = "点击成功"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class PositionClickService:
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

    def click_position_once(
        self,
        x: int,
        y: int,
        delay_seconds: float = 0.0,
        click_times: int = 1,
        click_interval_sec: float = 0.08,
    ) -> PositionClickResult:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        self._click(x, y, click_times=click_times, click_interval_sec=click_interval_sec)
        return PositionClickResult(ok=True, x=int(x), y=int(y))
