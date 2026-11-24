"""Station image decoding and dispatch, independent of the workbench and export UI."""
from PIL import Image

from engine import MAX_STATION_PHOTOS, decode, process_station
from guided import capture_spec, process_guided

MAX_FILE = 32 * 1024 * 1024
MAX_STATION_BYTES = 35 * 1024 * 1024


def prepare_station(name, files, spherical=False, capture=None):
    if capture is not None:
        _, poses = capture_spec(capture)
        if len(files) != len(poses):
            raise ValueError("照片张数需要和拍摄记录一致")
    elif not 1 <= len(files) <= MAX_STATION_PHOTOS:
        raise ValueError(f"每站请选择 1–{MAX_STATION_PHOTOS} 张照片")
    if sum(len(content) for _, content in files) > MAX_STATION_BYTES:
        raise ValueError("每站原始文件合计最多 35 MB")
    spherical = capture is None and spherical
    imgs = []
    for filename, content in files:
        try:
            if len(content) > MAX_FILE:
                raise ValueError("每张原图最多 32 MB")
            imgs.append((filename, decode(content, spherical, max_edge=1400 if capture is not None else 2400)))
        except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, ValueError) as e:
            if isinstance(e, (Image.DecompressionBombError, Image.DecompressionBombWarning)):
                detail = "图片像素超过 100 MP（1 亿像素）处理上限；请使用相机导出的较小原尺寸档"
            else:
                detail = str(e) if type(e) is ValueError else "无法读取，请上传 JPG、PNG 或 WebP"
            raise ValueError(f"{filename}：{detail}") from e
    return process_guided(name, imgs, capture) if capture is not None else process_station(name, imgs, spherical)
