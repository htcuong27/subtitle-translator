# Video Translate Super

Công cụ tạo phụ đề tiếng Việt cho video tiếng Trung, kết hợp nhận dạng giọng nói, OCR và dịch bằng AI. Ứng dụng chạy trên máy, có giao diện web để chọn vùng phụ đề và chỉnh sửa SRT, cùng CLI để xử lý theo lô.

> Dự án tạo và xuất phụ đề; chưa hỗ trợ lồng tiếng. Chất lượng nhận dạng và bản dịch phụ thuộc video, model và ngữ cảnh, vì vậy hãy kiểm tra trước khi sử dụng.

## Tính năng

- **Nhận dạng phụ đề:** dùng track phụ đề trong video, Whisper kết hợp RapidOCR, chỉ OCR hoặc chỉ Whisper.
- **Dịch bằng AI:** hỗ trợ Gemini, OpenAI, Anthropic và API tương thích OpenAI. Gemini hỗ trợ key và model dự phòng.
- **Giao diện cục bộ:** chọn nhiều video, khoanh vùng OCR riêng cho từng video, kiểm tra kết quả và theo dõi hàng đợi.
- **Chỉnh sửa và xuất:** sửa SRT trên video, xem thử clip ngắn, xuất SRT, softsub hoặc burn phụ đề vào video.
- **Tiếp tục công việc:** dùng lại phụ đề và kết quả đã hoàn tất khi chạy lại cùng nguồn và thư mục đầu ra.

## Yêu cầu

- Python **3.11 trở lên** và [uv](https://docs.astral.sh/uv/).
- `ffmpeg` và `ffprobe` trong `PATH`. Để burn phụ đề, FFmpeg cần filter `subtitles` và libass.
- Tài khoản và model AI phù hợp, hoặc một endpoint tương thích OpenAI chạy cục bộ.
- macOS để dùng nút chọn thư mục trong giao diện. CLI dùng shell và khóa POSIX; dự án chưa được kiểm thử trên Windows.

Các script ưu tiên FFmpeg tại `/opt/homebrew/opt/ffmpeg-full/bin` nếu có. `uv` cài dependency theo `uv.lock`; lần đầu dùng Whisper có thể cần tải model. API AI có thể tính phí theo tài khoản.

## Bắt đầu nhanh

### 1. Clone và chạy

```sh
git clone https://github.com/htcuong27/video-translate-super.git
cd video-translate-super
./ui.sh
```

Giao diện chạy trên `127.0.0.1` và tự mở trong trình duyệt. Để chọn cổng hoặc không mở trình duyệt:

```sh
./ui.sh --port 7867 --no-browser
```

### 2. Cấu hình AI

Mở **Cài đặt AI · API key và model**, chọn nhà cung cấp, nhập model và API key, sau đó lưu. Nút **Test kết nối** gửi một yêu cầu ngắn tới model đã chọn. Với API tương thích OpenAI, nhập Base URL trước `/chat/completions`, chẳng hạn `https://api.example.com/v1`; HTTP chỉ được chấp nhận cho endpoint cục bộ.

Cấu hình được lưu trong `.ai-settings.json` trên máy và dùng chung cho Web UI và CLI. Với Gemini, có thể thêm tối đa 10 key dự phòng. Khi key bị giới hạn HTTP 429, ứng dụng thử key khác và, tùy lỗi, model dự phòng.

Nếu không lưu cấu hình qua giao diện, có thể tạo `.env` ở thư mục dự án cho Gemini:

```dotenv
GEMINI_API_KEY=your_api_key
GEMINI_MODEL=your_model
GEMINI_FALLBACK_MODELS=
WHISPER_MODEL=base
```

Chọn model mà tài khoản có quyền sử dụng. `GEMINI_FALLBACK_MODELS` nhận danh sách model cách nhau bằng dấu phẩy; để trống để tắt. Khi sử dụng Whisper, `WHISPER_MODEL=small` hoặc `medium` có thể cải thiện nhận dạng nhưng chạy lâu hơn.

### 3. Xử lý video

1. Chọn thư mục video MP4 và mở một video trong giao diện.
2. Kiểm tra vùng OCR tự dò; thêm hoặc chỉnh vùng nếu cần, rồi dùng **Test OCR Ngay**.
3. Chọn chế độ nhận dạng, chế độ xuất và thư mục đầu ra. Web UI mặc định dùng **Chỉ OCR**.
4. Chọn video cần xử lý và bắt đầu hàng đợi. Sau khi có SRT, có thể chỉnh chữ, thời gian và xem thử trước khi xuất.

## Dùng CLI

```sh
# Liệt kê video mà không tải model hoặc gọi API
./translate.sh --input /duong-dan/video --list

# Tạo SRT cho một thư mục hoặc một video MP4
./translate.sh --input /duong-dan/video
./translate.sh --input /duong-dan/video/ep-001.mp4

# Xuất video với phụ đề có thể bật/tắt hoặc phụ đề in vào hình
./translate.sh --input /duong-dan/video --mode soft
./translate.sh --input /duong-dan/video --mode burn
```

Nếu không chỉ định `--input`, CLI tìm video trong `videos/` ở thư mục dự án. Dùng `--output /duong-dan/ket-qua` để chọn nơi lưu; thư mục đầu ra phải nằm ngoài thư mục nguồn.

| Tùy chọn | Tác dụng |
| --- | --- |
| `--mode srt` | Chỉ tạo phụ đề SRT; đây là mặc định. |
| `--mode soft` | Thêm phụ đề vào video dưới dạng track có thể bật/tắt. |
| `--mode burn` hoặc `--mode srt_burn` | In phụ đề vào hình video. |
| `--ocr-only` | Đọc chữ trên hình và suy ra thời gian từ các khung hình. |
| `--no-ocr` | Bỏ qua OCR; dùng track phụ đề có sẵn hoặc Whisper. |
| `--crop-box ymin,ymax,xmin,xmax` | Chỉ định vùng OCR theo tỉ lệ `0.0–1.0` hoặc pixel. |

Ví dụ chỉ nhận dạng vùng gần mép dưới video:

```sh
./translate.sh --input /duong-dan/video --ocr-only --crop-box 0.78,0.96,0.12,0.88
```

CLI mặc định dùng track phụ đề đầu tiên nếu có; nếu không, ứng dụng kết hợp Whisper và OCR. Xem đầy đủ tùy chọn bằng `./translate.sh --help`.

## Kết quả và tiếp tục công việc

Với đầu vào là một video `ep-001.mp4` và đầu ra mặc định, các tệp được lưu như sau:

```text
outputs/
└── ep-001/
    ├── source.json
    ├── srt.done
    └── outputs/
        ├── ep-001.zh.srt
        ├── ep-001.vi.srt
        └── ep-001.vi.burned.mp4   # khi chọn burn
```

Với đầu vào là thư mục, cấu trúc thư mục con được giữ trong `outputs/`. Nhóm nhiều video trong Web UI cũng giữ đường dẫn tương đối từ thư mục cha chung; thay nhóm nguồn có thể thay đổi vị trí cache. Tên video chỉ khác chữ hoa/thường được coi là trùng để tránh ghi đè.

Ứng dụng kiểm tra SHA-256 để phát hiện video nguồn thay đổi. Nếu đã có `.vi.srt` không rỗng, ứng dụng dùng lại bản dịch khi xuất, kể cả sau khi đổi model hoặc cấu hình OCR. Muốn dịch lại, xóa bản `.vi.srt` và các đầu ra/dấu hoàn tất tương ứng của video đó. `.zh.srt` là transcript nguồn, vẫn cần dịch để tạo phụ đề tiếng Việt.

Hàng đợi xử lý từng video; video lỗi được đánh dấu và các video tiếp theo vẫn được xử lý. Có thể dừng và chạy lại để tiếp tục. Trạng thái hàng đợi nằm trong bộ nhớ và không tự phục hồi sau khi khởi động lại server.

## Quyền riêng tư và giới hạn

- Video được xử lý trên máy và được giữ nguyên. Văn bản cần dịch và yêu cầu kiểm tra kết nối được gửi tới nhà cung cấp AI đã chọn. Font giao diện tải từ Google Fonts.
- `.env`, `.ai-settings.json`, `outputs/`, `videos/` và video MP4 được Git bỏ qua. Không đưa API key, video hoặc dữ liệu cá nhân vào commit.
- Chế độ softsub và burn không xóa chữ đã dính vào hình gốc. OCR có thể bỏ sót chữ, đặc biệt khi vùng chọn hoặc chất lượng video không phù hợp.
- Cấu hình mặc định trong mã không bảo đảm model còn khả dụng với tài khoản của bạn. Kiểm tra [model Gemini](https://ai.google.dev/gemini-api/docs/models) và [lịch ngừng hỗ trợ](https://ai.google.dev/gemini-api/docs/deprecations) khi cần.

## Tiện ích OCR màn hình

```sh
./ocr.sh
```

Đây là tiện ích riêng để chọn vùng màn hình và sao chép chữ, không phải OCR video của Web UI. Cần Python có Tkinter, Tesseract và dữ liệu ngôn ngữ `vie` + `eng`; trên macOS có thể cần cấp quyền chụp màn hình.

## Phát triển và kiểm thử

GitHub Actions chạy các kiểm thử dưới đây trên Ubuntu (Python 3.11) và macOS (Python 3.13) khi push hoặc tạo/cập nhật pull request.

```sh
uv run python -m unittest -v
node --test tests/*.cjs
node --check web/app.js
```

| Thành phần | Vai trò |
| --- | --- |
| `web/`, `web_ui.py`, `web_runner.py` | Giao diện, HTTP server và hàng đợi. |
| `translate.py`, `engine.py` | Xử lý theo lô, cache và điều phối từng video. |
| `recognition.py`, `gemini.py` | Nhận dạng, dịch AI và xử lý lỗi dịch vụ. |
| `subtitles.py`, `subtitle_editor.py`, `media.py` | SRT, chỉnh sửa và thao tác FFmpeg. |
| `tests/` | Kiểm thử Python và JavaScript chạy offline. |

## License

Mã nguồn của dự án được phát hành theo [MIT License](LICENSE). Các thư viện, model và dịch vụ bên thứ ba có điều khoản sử dụng riêng.
