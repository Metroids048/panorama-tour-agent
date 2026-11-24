"""Keep photographed detail in confirmed sphere previews without moving anchors."""
import base64
import io
import re

from PIL import Image, ImageOps

SCAN_MARKER = re.compile(rb"\xff+(?![\xff\x00\xd0-\xd7])")


def private_metadata_free_jpeg(data):
    """Keep the primary JPEG frame, including all its scans and color profile."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("JPEG header missing")
    output = bytearray(data[:2])
    offset = 2
    while offset < len(data):
        start = offset
        if data[offset] != 0xff:
            raise ValueError("Invalid JPEG marker")
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xd9:
            output.extend(data[start:offset])
            return bytes(output)
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset:offset + 2], "big")
        end = offset + length
        if length < 2 or end > len(data):
            break
        payload = data[offset + 2:end]
        keep = not (0xe0 <= marker <= 0xef or marker == 0xfe)
        keep |= marker == 0xe0 and payload.startswith(b"JFIF\0")
        keep |= marker == 0xe2 and payload.startswith(b"ICC_PROFILE\0")
        keep |= marker == 0xee and payload.startswith(b"Adobe")
        if keep:
            output.extend(data[start:end])
        offset = end
        if marker == 0xda:
            next_marker = SCAN_MARKER.search(data, offset)
            if next_marker is None or next_marker.end() == len(data):
                break
            offset = next_marker.start()
            output.extend(data[end:offset])
    raise ValueError("Incomplete primary JPEG")


def enhance_sphere(result, files, spherical):
    if not spherical or len(files) != 1 or not result["panorama"]:
        return result
    # Preparation has already validated the input and its decoded pixel budget.
    with Image.open(io.BytesIO(files[0][1])) as source:
        w, h = source.size
        mime = {"PNG": "image/png", "WEBP": "image/webp"}.get(source.format)
        metadata = {"exif", "xmp", "XML:com.adobe.xmp", "comment", "icc_profile"}
        orientation = source.getexif().get(274, 1)
        if source.format in {"JPEG", "MPO"} and w == 2 * h and orientation == 1:
            try:
                data = private_metadata_free_jpeg(files[0][1])
            except ValueError:
                # Keep the decoded view if a readable camera file uses an unusual marker layout.
                pass
            else:
                texture = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
                original = dict(result["originals"][0], panorama_source=True)
                return dict(result, originals=[original], panorama=dict(result["panorama"], image=texture, width=w, height=h))
        if mime and w == 2 * h and orientation == 1 and not metadata.intersection(source.info):
            texture = f"data:{mime};base64," + base64.b64encode(files[0][1]).decode("ascii")
            original = dict(result["originals"][0], panorama_source=True)
            return dict(result, originals=[original], panorama=dict(result["panorama"], image=texture, width=w, height=h))
        if source.getexif().get(274) in (5, 6, 7, 8):
            w, h = h, w
        delta = abs(w - 2 * h)
        if w <= 3840 or 0 < delta <= 2 or delta > w * 0.002:
            return result
        sphere = ImageOps.exif_transpose(source).convert("RGB")
        if delta:
            sphere = sphere.resize((w, round(w / 2)), Image.Resampling.LANCZOS)
        # Export pixels only: camera metadata may contain location information.
        data = io.BytesIO()
        sphere.save(data, "JPEG", quality=92)
    texture = "data:image/jpeg;base64," + base64.b64encode(data.getvalue()).decode("ascii")
    original = dict(result["originals"][0], panorama_source=True)
    return dict(result, originals=[original], panorama=dict(result["panorama"], image=texture,
                                      width=sphere.width, height=sphere.height))
