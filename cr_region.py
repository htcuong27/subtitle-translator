"""
Công cụ OCR theo vùng chọn (Windows/macOS/Linux).

Cách dùng:
  1. Chạy ./ocr.sh để dùng dependencies theo uv.lock (Python cần có Tkinter).
  2. Cài Tesseract OCR (Windows: https://github.com/UB-Mannheim/tesseract/wiki
     -> nhớ tick gói ngôn ngữ Vietnamese) rồi sửa TESSERACT_PATH nếu cần.
  3. ./ocr.sh (hoặc uv run python cr_region.py)
  4. Bấm "Chọn vùng", kéo chuột khoanh vùng chứa chữ, thả chuột -> kết quả hiện ra.
     Nhấn Esc để hủy chọn.
"""
import tkinter as tk
from tkinter import ttk
from PIL import ImageGrab, ImageOps, ImageTk, Image
import pytesseract

# Windows: bỏ comment và sửa đường dẫn nếu Tesseract chưa nằm trong PATH
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

LANG = "vie+eng"  # đổi thành "eng" nếu chỉ cần tiếng Anh


def preprocess(img: Image.Image, scale: float = 2.0) -> Image.Image:
    """Phóng to + chuyển xám + tăng tương phản: giúp OCR chính xác hơn rõ rệt."""
    img = img.convert("L")
    img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    return img


class RegionSelector(tk.Toplevel):
    """Cửa sổ toàn màn hình mờ để kéo chọn vùng."""

    def __init__(self, master, screenshot, on_done):
        super().__init__(master)
        self.screenshot = screenshot
        self.on_done = on_done
        self.start = None
        self.rect = None

        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.config(cursor="crosshair")

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.bg = ImageTk.PhotoImage(screenshot)
        self.canvas.create_image(0, 0, anchor="nw", image=self.bg)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Escape>", lambda e: self.destroy())

    def on_press(self, e):
        self.start = (e.x, e.y)
        self.rect = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline="red", width=2
        )

    def on_drag(self, e):
        if self.rect:
            self.canvas.coords(self.rect, self.start[0], self.start[1], e.x, e.y)

    def on_release(self, e):
        x1, y1 = self.start
        x2, y2 = e.x, e.y
        box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self.destroy()
        if box[2] - box[0] > 5 and box[3] - box[1] > 5:
            self.on_done(self.screenshot.crop(box))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OCR theo vùng chọn")
        self.geometry("560x520")

        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)
        ttk.Button(top, text="Chọn vùng (kéo chuột)", command=self.select).pack(side="left")
        ttk.Button(top, text="Copy kết quả", command=self.copy).pack(side="left", padx=8)

        ttk.Label(top, text="Chế độ:").pack(side="left", padx=(16, 4))
        self.psm = tk.StringVar(value="6")
        ttk.Combobox(
            top, textvariable=self.psm, width=4, state="readonly",
            values=["3", "6", "7", "11"],
        ).pack(side="left")
        ttk.Label(top, text="(6=khối chữ, 7=1 dòng)").pack(side="left", padx=4)

        self.preview = ttk.Label(self)
        self.preview.pack(padx=10, pady=4)

        self.text = tk.Text(self, wrap="word", height=14)
        self.text.pack(fill="both", expand=True, padx=10, pady=10)

    def select(self):
        self.withdraw()           # ẩn cửa sổ chính để không bị chụp vào
        self.after(250, self._grab_and_select)

    def _grab_and_select(self):
        shot = ImageGrab.grab()
        self.deiconify()
        RegionSelector(self, shot, self.run_ocr)

    def run_ocr(self, crop: Image.Image):
        # hiển thị ảnh vùng đã chọn
        thumb = crop.copy()
        thumb.thumbnail((520, 120))
        self._thumb = ImageTk.PhotoImage(thumb)
        self.preview.config(image=self._thumb)

        img = preprocess(crop)
        config = f"--oem 3 --psm {self.psm.get()}"
        result = pytesseract.image_to_string(img, lang=LANG, config=config).strip()

        self.text.delete("1.0", "end")
        self.text.insert("1.0", result)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end").strip())


if __name__ == "__main__":
    App().mainloop()