"""
Generate Dexter app icon in PNG and multi-resolution ICO formats.
Matches Dexter's brand: Indigo rounded squircle (#4f46e5) with a bold white 'D'.
"""

import os
from PIL import Image, ImageDraw, ImageFont

def generate_dexter_icons(output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer rounded rectangle (squircle)
    margin = 48
    corner_radius = 220
    rect_box = [margin, margin, size - margin, size - margin]

    # Gradient-like subtle layering: background squircle
    # Main indigo background #4338ca -> #4f46e5 -> #6366f1
    draw.rounded_rectangle(rect_box, radius=corner_radius, fill=(79, 70, 229, 255))

    # Inner subtle glow border
    inner_margin = margin + 12
    draw.rounded_rectangle(
        [inner_margin, inner_margin, size - inner_margin, size - inner_margin],
        radius=corner_radius - 10,
        outline=(129, 140, 248, 120),
        width=12
    )

    # Draw the modern stylized "D" in white
    # Fallback to system fonts (Segoe UI, Arial) or geometric drawing if font unavailable
    font_drawn = False
    for font_name in ["segoeuib.ttf", "arialbd.ttf", "Segoe UI.ttf", "Arial.ttf"]:
        try:
            # Look in Windows Fonts folder
            font_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", font_name)
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, 600)
                # Calculate text position to center it
                bbox = draw.textbbox((0, 0), "D", font=font)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                x = (size - w) / 2 - bbox[0]
                y = (size - h) / 2 - bbox[1] - 10
                draw.text((x, y), "D", font=font, fill=(255, 255, 255, 255))
                font_drawn = True
                break
        except Exception:
            continue

    if not font_drawn:
        # Fallback: geometric modern D glyph
        dx1, dy1, dx2, dy2 = 280, 240, 740, 784
        thickness = 110
        # Left vertical spine
        draw.rounded_rectangle([dx1, dy1, dx1 + thickness, dy2], radius=30, fill=(255, 255, 255, 255))
        # Top bar
        draw.rounded_rectangle([dx1, dy1, dx2 - 120, dy1 + thickness], radius=20, fill=(255, 255, 255, 255))
        # Bottom bar
        draw.rounded_rectangle([dx1, dy2 - thickness, dx2 - 120, dy2], radius=20, fill=(255, 255, 255, 255))
        # Right curve
        draw.arc([dx2 - 380, dy1, dx2, dy2], start=270, end=90, fill=(255, 255, 255, 255), width=thickness)

    # Save high-resolution PNG
    png_path = os.path.join(output_dir, "icon.png")
    img.save(png_path, format="PNG")
    print(f"Generated {png_path}")

    # Generate multi-resolution ICO (256, 128, 64, 48, 32, 16)
    ico_path = os.path.join(output_dir, "icon.ico")
    icon_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(ico_path, format="ICO", sizes=icon_sizes)
    print(f"Generated {ico_path} with sizes {icon_sizes}")

if __name__ == "__main__":
    import sys
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets_dir = os.path.join(base_dir, "assets")
    generate_dexter_icons(assets_dir)
