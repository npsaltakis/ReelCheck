"""Φτιάχνει το icon.ico (κλακέτα/film με κουμπί play). Χρειάζεται Pillow."""
from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# σκούρο στρογγυλεμένο φόντο
d.rounded_rectangle((40, 40, S - 40, S - 40), radius=200, fill=(28, 30, 38, 255))
# λωρίδα φιλμ πάνω και κάτω
for y0 in (110, S - 250):
    d.rectangle((40, y0, S - 40, y0 + 140), fill=(12, 12, 16, 255))
    for x in range(95, S - 60, 120):
        d.rounded_rectangle((x, y0 + 35, x + 70, y0 + 105), radius=14, fill=(230, 230, 235, 255))
# πορτοκαλί κύκλος με play
cx, cy, r = S // 2, S // 2, 190
d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(240, 170, 40, 255))
d.polygon([(cx - 60, cy - 100), (cx - 60, cy + 100), (cx + 110, cy)], fill=(28, 30, 38, 255))
# κόψιμο στο στρογγυλεμένο σχήμα
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=200, fill=255)
img.putalpha(mask)
img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save("icon.png")
print("ok")
