import os
import sys
import json
from PIL import Image, ImageDraw, ImageFont


def load_annotation_font(size=24):
    """Load a font that can render Chinese labels on Windows, macOS and common Linux setups."""
    font_paths = [
        # Windows Chinese fonts
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        # macOS Chinese fonts
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Heiti SC.ttc",
        "/System/Library/Fonts/Supplemental/Hiragino Sans GB.ttc",
        # Common Linux Chinese fonts
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        # Last resort for English-only labels
        "/System/Library/Fonts/Helvetica.ttc",
    ]

    for font_path in font_paths:
        if not os.path.exists(font_path):
            continue
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            continue

    return ImageFont.load_default()


def normalize_color(color):
    color_map = {
        "red": "#dc3545",
        "orange": "#ff8a00",
        "yellow": "#f5b400",
        "blue": "#2364e8",
        "green": "#18a058",
    }
    return color_map.get(color, color)

def annotate_image(image_path, annotations, output_path=None):
    """
    annotations: list of dicts
    [
        {"rect": [x1, y1, x2, y2], "label": "P0-Error", "color": "red"},
        ...
    ]
    
    =============================================================================
    [WARNING FOR AI AGENTS] 
    DO NOT guess coordinates blindly! If you do not have exact coordinates, 
    use the `browser_subagent` to inject CSS highlighters into the DOM instead.
    
    If you MUST use this script, run this JS snippet in the browser console 
    to extract the exact absolute coordinates of the element:
    
    const el = document.querySelector('.your-selector');
    const rect = el.getBoundingClientRect();
    const x1 = rect.left + window.scrollX;
    const y1 = rect.top + window.scrollY;
    const x2 = x1 + rect.width;
    const y2 = y1 + rect.height;
    console.log(`"rect": [${Math.round(x1)}, ${Math.round(y1)}, ${Math.round(x2)}, ${Math.round(y2)}]`);
    =============================================================================
    """
    if not os.path.exists(image_path):
        print(f"Error: Image {image_path} not found.")
        return

    try:
        img = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(img)
        
        font = load_annotation_font(24)

        for ann in annotations:
            rect = ann.get("rect")
            label = ann.get("label", "")
            color = normalize_color(ann.get("color", "red"))
            if not rect or len(rect) != 4:
                print(f"Skip invalid annotation rect: {ann}")
                continue

            x1, y1, x2, y2 = [int(v) for v in rect]
            rect = [x1, y1, x2, y2]

            # Draw rectangle
            draw.rectangle(rect, outline=color, width=5)

            # Draw label background and keep it aligned with the label text.
            # Prefer placing the label above the rect; fall back to inside the rect if needed.
            label_x = x1
            label_y = y1 - 36 if y1 >= 40 else y1 + 6
            text_bbox = draw.textbbox((label_x, label_y), label, font=font)
            padding_x = 8
            padding_y = 5
            bg_rect = [
                text_bbox[0] - padding_x,
                text_bbox[1] - padding_y,
                text_bbox[2] + padding_x,
                text_bbox[3] + padding_y,
            ]
            draw.rectangle(bg_rect, fill=color)
            draw.text((label_x, label_y), label, fill="white", font=font)

        if not output_path:
            base, ext = os.path.splitext(image_path)
            output_path = f"{base}_annotated{ext}"

        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)
        img.save(output_path)
        print(f"Successfully saved annotated image to: {output_path}")
        return output_path

    except Exception as e:
        print(f"An error occurred: {e}")

DEFAULT_OUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'Reports', 'assets'
)


def resolve_output_path(image_path, out=None):
    """批注图默认落到 Reports/assets（与 SKILL.md §3 批注 SOP 一致）。

    out 允许是目录（自动沿用 `<原图名>_annotated.<ext>`）或完整文件路径。
    """
    stem, ext = os.path.splitext(os.path.basename(image_path))
    default_name = '%s_annotated%s' % (stem, ext or '.png')
    if not out:
        return os.path.join(DEFAULT_OUT_DIR, default_name)
    if os.path.isdir(out) or out.endswith(os.sep) or out.endswith('/'):
        return os.path.join(out, default_name)
    return out


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) < 2:
        print("Usage: python3 auto_annotate.py <image_path> '<json_annotations>' [--out <file_or_dir>]")
        print("Example: python3 auto_annotate.py screen.png "
              "'[{\"rect\": [10, 10, 100, 100], \"label\": \"P2-较重\", \"color\": \"orange\"}]'")
        print("默认输出：Reports/assets/<原图名>_annotated.<ext>")
        sys.exit(1)

    out = None
    if '--out' in argv:
        i = argv.index('--out')
        out = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]

    path = argv[0]
    data = json.loads(argv[1])
    annotate_image(path, data, resolve_output_path(path, out))
