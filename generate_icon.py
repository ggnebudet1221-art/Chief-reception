from PIL import Image, ImageDraw, ImageFont

size = 256

img = Image.new("RGBA", (size, size), "#0f1117")
draw = ImageDraw.Draw(img)

# градиент
for y in range(size):
    r = 90 + y // 6
    g = 50
    b = 255
    draw.line((0, y, size, y), fill=(r, g, b))

# текст
text = "AI"

try:
    font = ImageFont.truetype("arial.ttf", 120)
except:
    font = ImageFont.load_default()

bbox = draw.textbbox((0, 0), text, font=font)
w = bbox[2] - bbox[0]
h = bbox[3] - bbox[1]

draw.text(
    ((size - w) / 2, (size - h) / 2 - 10),
    text,
    fill="white",
    font=font
)

img.save(
    "src-tauri/icons/icon.ico",
    format="ICO",
    sizes=[(256,256), (128,128), (64,64), (32,32)]
)

print("icon.ico created")