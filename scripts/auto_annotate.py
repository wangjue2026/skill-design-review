import os
import sys
import json
from PIL import Image, ImageDraw, ImageFont


def load_annotation_font(size=24):
    """Load a font that can render Chinese labels on macOS and common Linux setups."""
    font_paths = [
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

        img.save(output_path)
        print(f"Successfully saved annotated image to: {output_path}")
        return output_path

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python auto_annotate.py <image_path> '<json_annotations>'")
        print("Example: python auto_annotate.py screen.png '[{\"rect\": [10, 10, 100, 100], \"label\": \"P1-严重\", \"color\": \"red\"}]'")
    else:
        path = sys.argv[1]
        data = json.loads(sys.argv[2])
        annotate_image(path, data)
