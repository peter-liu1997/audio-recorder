#!/usr/bin/env python3
"""Generate the native macOS app icon (requires Pillow and macOS iconutil)."""

from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter


def main():
    size = 1024
    image = Image.new("RGBA", (size, size))
    shadow = Image.new("RGBA", (size, size))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((80, 85, 952, 957), radius=230, fill=(0, 19, 35, 190))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(34)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((64, 55, 960, 951), radius=235, fill="#112d42")
    draw.rounded_rectangle((95, 86, 929, 920), radius=205, outline="#28576a", width=11)
    glow = Image.new("RGBA", (size, size))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((230, 230, 794, 794), fill=(57, 212, 184, 70))
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(48)))
    draw = ImageDraw.Draw(image)
    draw.ellipse((252, 252, 772, 772), outline="#42c8ad", width=31)
    draw.ellipse((278, 278, 746, 746), outline="#65dfc7", width=5)
    heights = [118, 244, 352, 244, 118]
    for index, height in enumerate(heights):
        x = 356 + index * 78
        draw.rounded_rectangle((x, 512 - height // 2, x + 46, 512 + height // 2),
                               radius=23, fill="#a2f4df")
    draw.ellipse((735, 215, 766, 246), fill="#aff9e6")
    draw.ellipse((787, 260, 805, 278), fill="#6adcc4")
    output = Path(__file__).resolve().parents[1] / "Resources/MeetingAssistant.icns"
    with tempfile.TemporaryDirectory(prefix="meeting-icon-") as temporary:
        iconset = Path(temporary) / "MeetingAssistant.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            image.resize((base, base), Image.Resampling.LANCZOS).save(iconset / f"icon_{base}x{base}.png")
            image.resize((base * 2, base * 2), Image.Resampling.LANCZOS).save(
                iconset / f"icon_{base}x{base}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(output)], check=True)
    print(output)


if __name__ == "__main__":
    main()
