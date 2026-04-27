import base64
import ctypes
import hashlib
import json
import random
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

try:
    from PIL import Image, ImageGrab
except Exception:  # pragma: no cover
    Image = None
    ImageGrab = None


OCR_API_URL = "https://openapi.youdao.com/ocrapi"


@dataclass
class TextMatch:
    text: str
    score: Optional[float]
    left: int
    top: int
    right: int
    bottom: int
    center_x: int
    center_y: int
    left_screen: Optional[int] = None
    top_screen: Optional[int] = None
    right_screen: Optional[int] = None
    bottom_screen: Optional[int] = None
    center_x_screen: Optional[int] = None
    center_y_screen: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class YoudaoTextLocator:
    _dpi_awareness_enabled = False

    def __init__(self, config_path: Union[str, Path] = "set_ocr.txt", timeout: int = 20) -> None:
        self.config_path = Path(config_path)
        self.timeout = timeout
        self.config = self._load_config(self.config_path)
        self.app_key = self.config.get("youdao_appkey", "").strip()
        self.app_secret = self.config.get("youdao_appsecret", "").strip()
        self.ocr_lang = self.config.get("youdao_ocr_lang", "auto").strip() or "auto"
        if not self.app_key or not self.app_secret:
            raise ValueError("set_ocr.txt 缺少 youdao_appkey 或 youdao_appsecret")

    @staticmethod
    def enable_dpi_awareness() -> None:
        if YoudaoTextLocator._dpi_awareness_enabled:
            return
        user32 = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            YoudaoTextLocator._dpi_awareness_enabled = True
        except Exception:
            try:
                user32.SetProcessDPIAware()
                YoudaoTextLocator._dpi_awareness_enabled = True
            except Exception:
                pass

    @staticmethod
    def get_virtual_screen_region() -> Tuple[int, int, int, int]:
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        return left, top, left + width, top + height

    @staticmethod
    def map_bbox_to_screen(
        bbox: Tuple[int, int, int, int],
        image_size: Tuple[int, int],
        capture_region: Tuple[int, int, int, int],
    ) -> Tuple[int, int, int, int]:
        left, top, right, bottom = bbox
        image_w, image_h = image_size
        cap_left, cap_top, cap_right, cap_bottom = capture_region
        cap_w = max(1, cap_right - cap_left)
        cap_h = max(1, cap_bottom - cap_top)
        scale_x = cap_w / max(1, image_w)
        scale_y = cap_h / max(1, image_h)
        s_left = int(cap_left + left * scale_x)
        s_top = int(cap_top + top * scale_y)
        s_right = int(cap_left + right * scale_x)
        s_bottom = int(cap_top + bottom * scale_y)
        return s_left, s_top, s_right, s_bottom

    @staticmethod
    def _load_config(path: Path) -> Dict[str, str]:
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")
        cfg: Dict[str, str] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            cfg[key.strip()] = value.strip()
        return cfg

    @staticmethod
    def _truncate(text: str) -> str:
        if text is None:
            return ""
        if len(text) <= 20:
            return text
        return text[:10] + str(len(text)) + text[-10:]

    def _build_sign(self, img_base64: str, salt: str, curtime: str) -> str:
        sign_str = f"{self.app_key}{self._truncate(img_base64)}{salt}{curtime}{self.app_secret}"
        return hashlib.sha256(sign_str.encode("utf-8")).hexdigest()

    @staticmethod
    def _image_to_base64(image_source: Union[str, Path, bytes, "Image.Image"]) -> str:
        if isinstance(image_source, (str, Path)):
            data = Path(image_source).read_bytes()
            return base64.b64encode(data).decode("utf-8")
        if isinstance(image_source, bytes):
            return base64.b64encode(image_source).decode("utf-8")
        if Image is not None and hasattr(image_source, "save"):
            from io import BytesIO

            buf = BytesIO()
            image_source.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        raise TypeError("image_source 必须是图片路径、bytes 或 PIL.Image 对象")

    @staticmethod
    def capture_screen(region: Optional[Tuple[int, int, int, int]] = None) -> "Image.Image":
        if ImageGrab is None:
            raise RuntimeError("当前环境未安装 Pillow，无法截图。请先安装 pillow")
        # region: (left, top, right, bottom)
        return ImageGrab.grab(bbox=region)

    def ocr(self, image_source: Union[str, Path, bytes, "Image.Image"]) -> Dict[str, Any]:
        img_base64 = self._image_to_base64(image_source)
        salt = str(random.randint(10000, 99999))
        curtime = str(int(time.time()))
        sign = self._build_sign(img_base64, salt, curtime)

        payload = {
            "img": img_base64,
            "langType": self.ocr_lang,
            "detectType": "10012",
            "imageType": "1",
            "docType": "json",
            "signType": "v3",
            "curtime": curtime,
            "appKey": self.app_key,
            "salt": salt,
            "sign": sign,
        }
        resp = requests.post(OCR_API_URL, data=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        error_code = str(data.get("errorCode", ""))
        if error_code and error_code != "0":
            raise RuntimeError(f"有道 OCR 返回错误: errorCode={error_code}, full={json.dumps(data, ensure_ascii=False)}")
        return data

    @staticmethod
    def _extract_bbox(node: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
        if not isinstance(node, dict):
            return None

        # Youdao OCR 常见格式: "boundingBox": "x1,y1,x2,y2,x3,y3,x4,y4"
        bounding_box = node.get("boundingBox")
        if isinstance(bounding_box, str) and bounding_box.strip():
            parts = [p.strip() for p in bounding_box.split(",") if p.strip()]
            if len(parts) >= 8:
                try:
                    coords = [float(v) for v in parts[:8]]
                    xs = coords[0::2]
                    ys = coords[1::2]
                    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
                except ValueError:
                    pass

        for left_k, top_k, width_k, height_k in [
            ("x", "y", "width", "height"),
            ("left", "top", "width", "height"),
        ]:
            if all(k in node for k in (left_k, top_k, width_k, height_k)):
                left = int(float(node[left_k]))
                top = int(float(node[top_k]))
                width = int(float(node[width_k]))
                height = int(float(node[height_k]))
                return left, top, left + width, top + height

        if all(k in node for k in ("left", "top", "right", "bottom")):
            return (
                int(float(node["left"])),
                int(float(node["top"])),
                int(float(node["right"])),
                int(float(node["bottom"])),
            )

        vertices = node.get("vertices") or node.get("points") or node.get("vertexes")
        if isinstance(vertices, list) and vertices:
            xs: List[float] = []
            ys: List[float] = []
            for p in vertices:
                if isinstance(p, dict):
                    if "x" in p and "y" in p:
                        xs.append(float(p["x"]))
                        ys.append(float(p["y"]))
                elif isinstance(p, (list, tuple)) and len(p) >= 2:
                    xs.append(float(p[0]))
                    ys.append(float(p[1]))
            if xs and ys:
                return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        return None

    @classmethod
    def _collect_text_items(cls, obj: Any) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                text_value = None
                for key in ("text", "words", "word", "content", "lineText"):
                    if key in node and isinstance(node[key], str):
                        text_value = node[key]
                        break
                bbox = cls._extract_bbox(node)
                if text_value and bbox:
                    found.append(
                        {
                            "text": text_value,
                            "bbox": bbox,
                            "score": node.get("score") or node.get("confidence"),
                        }
                    )
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(obj)
        return found

    @staticmethod
    def _norm_text(text: str) -> str:
        return "".join(text.split())

    def locate_text(
        self,
        image_source: Union[str, Path, bytes, "Image.Image"],
        target_text: str,
        exact_match: bool = False,
        case_sensitive: bool = False,
    ) -> List[TextMatch]:
        if not target_text or not target_text.strip():
            raise ValueError("target_text 不能为空")

        result = self.ocr(image_source)
        items = self._collect_text_items(result)

        keyword = target_text if case_sensitive else target_text.lower()
        keyword_cmp = self._norm_text(keyword)
        matches: List[TextMatch] = []

        for item in items:
            raw_text = str(item["text"])
            candidate = raw_text if case_sensitive else raw_text.lower()
            candidate_cmp = self._norm_text(candidate)
            hit = candidate_cmp == keyword_cmp if exact_match else (keyword_cmp in candidate_cmp)
            if not hit:
                continue
            left, top, right, bottom = item["bbox"]
            center_x = int((left + right) / 2)
            center_y = int((top + bottom) / 2)
            matches.append(
                TextMatch(
                    text=raw_text,
                    score=float(item["score"]) if item.get("score") is not None else None,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                    center_x=center_x,
                    center_y=center_y,
                )
            )
        return matches

    def locate_text_on_screen(
        self,
        target_text: str,
        region: Optional[Tuple[int, int, int, int]] = None,
        exact_match: bool = False,
        case_sensitive: bool = False,
    ) -> List[TextMatch]:
        self.enable_dpi_awareness()
        capture_region = region or self.get_virtual_screen_region()
        shot = self.capture_screen(region=capture_region)
        matches = self.locate_text(
            image_source=shot,
            target_text=target_text,
            exact_match=exact_match,
            case_sensitive=case_sensitive,
        )
        image_size = shot.size
        enriched: List[TextMatch] = []
        for m in matches:
            screen_bbox = self.map_bbox_to_screen(
                bbox=(m.left, m.top, m.right, m.bottom),
                image_size=image_size,
                capture_region=capture_region,
            )
            s_left, s_top, s_right, s_bottom = screen_bbox
            enriched.append(
                TextMatch(
                    text=m.text,
                    score=m.score,
                    left=m.left,
                    top=m.top,
                    right=m.right,
                    bottom=m.bottom,
                    center_x=m.center_x,
                    center_y=m.center_y,
                    left_screen=s_left,
                    top_screen=s_top,
                    right_screen=s_right,
                    bottom_screen=s_bottom,
                    center_x_screen=int((s_left + s_right) / 2),
                    center_y_screen=int((s_top + s_bottom) / 2),
                )
            )
        return enriched

    def locate_text_from_screenshot(
        self,
        target_text: str,
        region: Optional[Tuple[int, int, int, int]] = None,
        exact_match: bool = False,
        case_sensitive: bool = False,
    ) -> List[TextMatch]:
        shot = self.capture_screen(region=region)
        return self.locate_text(
            image_source=shot,
            target_text=target_text,
            exact_match=exact_match,
            case_sensitive=case_sensitive,
        )


def locate_text(
    image_source: Union[str, Path, bytes, "Image.Image"],
    target_text: str,
    config_path: Union[str, Path] = "set_ocr.txt",
    exact_match: bool = False,
    case_sensitive: bool = False,
) -> List[Dict[str, Any]]:
    locator = YoudaoTextLocator(config_path=config_path)
    return [m.to_dict() for m in locator.locate_text(image_source, target_text, exact_match, case_sensitive)]

