import os
import sys
import json
from PIL import Image, ImageDraw, ImageFont

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
        
        # Try to load a font, fallback to default
        try:
            # Use a common system font for Mac
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        except:
            font = ImageFont.load_default()

        for ann in annotations:
            rect = ann.get("rect")
            label = ann.get("label", "")
            color = ann.get("color", "red")
            
            # Draw rectangle
            draw.rectangle(rect, outline=color, width=5)
            
            # Draw label background
            text_size = draw.textbbox((rect[0], rect[1]), label, font=font)
            draw.rectangle([text_size[0]-5, text_size[1]-5, text_size[2]+5, text_size[3]+5], fill=color)
            
            # Draw text
            draw.text((rect[0], rect[1]-30), label, fill="white", font=font)

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
        print("Example: python auto_annotate.py screen.png '[{\"rect\": [10, 10, 100, 100], \"label\": \"P0\", \"color\": \"red\"}]'")
    else:
        path = sys.argv[1]
        data = json.loads(sys.argv[2])
        annotate_image(path, data)
