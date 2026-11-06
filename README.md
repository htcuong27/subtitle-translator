# Video Translate Super

Dịch video tiếng Trung sang phụ đề tiếng Việt bằng Whisper, OCR và dịch vụ AI bạn chọn. Dự án chạy độc lập, giữ nguyên video gốc và có giao diện web cục bộ cùng CLI.

Cần Python 3.11+, [uv](https://docs.astral.sh/uv/), ffmpeg và ffprobe. Chế độ `burn` cần ffmpeg có filter `subtitles`/libass (script ưu tiên bản Homebrew `ffmpeg-full`). Các script dùng shell và khóa POSIX; dự án chưa được kiểm thử trên Windows.

Giao diện chạy trên `127.0.0.1`; nút chọn thư mục dùng hộp thoại macOS. Trên hệ điều hành khác, dùng CLI để chỉ định thư mục nguồn. Font giao diện được tải từ Google Fonts; dịch và kiểm tra kết nối gửi nội dung cần xử lý tới nhà cung cấp AI đã chọn.

Sau khi clone, chạy từ thư mục dự án. Chuẩn bị video MP4 ở một thư mục riêng và chọn thư mục đó trong giao diện, hoặc truyền đường dẫn bằng `--input`. Nếu bỏ `--input`, CLI tìm thư mục `videos/` trong dự án.

```sh
# 1. Chạy giao diện Web UI chọn vùng phụ đề trực quan (Khuyên dùng):
./ui.sh

# 2. Hoặc chạy qua dòng lệnh (CLI):
./translate.sh --input /duong-dan/video --list
./translate.sh --input /duong-dan/video
./translate.sh --input /duong-dan/video --mode soft
./translate.sh --input /duong-dan/video --mode burn
# Dùng vùng crop OCR tùy chỉnh (ymin,ymax,xmin,xmax theo tỉ lệ 0.0-1.0 hoặc pixel):
./translate.sh --input /duong-dan/video --crop-box 0.78,0.96,0.12,0.88
```

`uv` tự cài dependencies theo `uv.lock`. Lần đầu Whisper tải model; API AI có thể phát sinh phí theo tài khoản. `WHISPER_MODEL=small` hoặc `medium` tăng độ chính xác nhưng chạy lâu hơn. CLI mặc định dùng track phụ đề đầu tiên nếu có, nếu không thì dùng Whisper và OCR; `--ocr-only` chỉ đọc chữ trên hình, còn `--no-ocr` tắt OCR. Web UI mặc định chọn chế độ chỉ OCR. Trong UI, kéo chuột khoanh vùng phụ đề và test OCR trước khi chạy. Tham số `--crop-box ymin,ymax,xmin,xmax` chỉ định vùng quét OCR bằng tỉ lệ hoặc pixel. Dùng `./translate.sh --help` để xem các tùy chọn.

Trong Web UI, mở **Cài đặt AI · API key và model**. Chọn Gemini, OpenAI (GPT), Anthropic (Claude) hoặc API tương thích OpenAI, nhập mã model và API key rồi bấm **Lưu cài đặt AI**. Với API tương thích, nhập Base URL trước phần `/chat/completions`, ví dụ `https://api.example.com/v1`. Endpoint cục bộ có thể chạy qua HTTP và để trống key nếu không cần xác thực.

**Test kết nối** gửi một yêu cầu ngắn tới đúng model đang nhập. Bạn có thể thử trước khi lưu. Để trống ô API key để thử key đã lưu. Nút kiểm tra chỉ thử key chính và model đang chọn.

Với Gemini, nhập tối đa 10 key dự phòng trong ô **Key Gemini dự phòng**, cách nhau bằng dấu phẩy. Khi dịch gặp HTTP 429, app thử key tiếp theo và tiếp tục dùng key đã hoạt động trong cùng lượt dịch video. Nếu tất cả key đều bị giới hạn, app thử model dự phòng đã cấu hình. Các key cùng dự án Google có thể dùng chung hạn mức.

Cấu hình đã lưu áp dụng cho hàng đợi, trình sửa phụ đề và CLI. Key do giao diện lưu nằm trong `.ai-settings.json` trên máy với quyền truy cập chỉ cho tài khoản đang chạy app. Nếu chưa thiết lập, app dùng Gemini và tiếp tục nhận `GEMINI_API_KEY` trong `.env`. Các file `.vi.srt` có sẵn tiếp tục được tái sử dụng khi đổi model.

Để cấu hình bằng môi trường, tạo file `.env` trong thư mục dự án và điền key cùng model có sẵn cho tài khoản:

```dotenv
GEMINI_API_KEY=your_api_key
GEMINI_MODEL=your_model
GEMINI_FALLBACK_MODELS=
WHISPER_MODEL=base
```

`.env`, `.ai-settings.json`, `outputs/`, `videos/`, `models/` và video MP4 được Git bỏ qua; giữ video, phụ đề và dữ liệu riêng tư ngoài repository và kiểm tra danh sách file trước khi commit.

Web UI tự dò vùng phụ đề tiếng Trung trên một số khung hình khi mở video chưa lưu vùng OCR. Nếu chưa tìm được vùng chữ, UI giữ vùng mặc định để bạn kiểm tra. Dùng **+ Vùng OCR** để thêm vị trí và **Xóa vùng** để xóa vùng đang chọn. Mỗi video giữ cấu hình riêng, kể cả khi chạy theo hàng đợi. Trong **Nhận dạng phụ đề**, chọn **Chỉ OCR (tắt Whisper)** để lấy chữ có sẵn trên hình và suy ra thời gian từ các khung hình. Chế độ này tương ứng với `--ocr-only` trên CLI.

Tên model mặc định trong mã là `gemini-3.8-flash`; danh sách dự phòng mặc định là `gemini-3.5-flash-lite`. Có thể đổi qua `GEMINI_MODEL` và `GEMINI_FALLBACK_MODELS` trong `.env` để phù hợp với model tài khoản được phép dùng. Khi model chính lỗi tạm thời (429/500/502/503/504), chương trình thử tối đa 3 lần rồi chuyển sang `GEMINI_FALLBACK_MODELS=gemini-3.5-flash-lite`. HTTP 404 chuyển ngay sang model tiếp theo; lỗi kết nối/timeout cũng thử tối đa 3 lần mỗi model. Danh sách dự phòng phân cách bằng dấu phẩy; để trống để tắt. Lỗi xác thực/quyền truy cập, phản hồi không hợp lệ hoặc không có bản dịch sẽ dừng, không chuyển model. Log ghi rõ model đang lỗi. Đổi model chính hoặc danh sách dự phòng không xóa phụ đề đã có.

Tên model trong cấu hình không xác nhận model đó đang khả dụng hoặc tài khoản có quyền truy cập. Khi gặp lỗi model, kiểm tra [danh sách model](https://ai.google.dev/gemini-api/docs/models) và [lịch ngừng hỗ trợ](https://ai.google.dev/gemini-api/docs/deprecations) trước khi đổi cấu hình.

Phản hồi Gemini dùng JSON schema yêu cầu đủ từng ID. OCR không thể đọc được đánh dấu `[Không rõ]`, không đoán lời thoại. Nếu Gemini trả JSON hợp lệ nhưng thiếu ID hoặc câu dịch rỗng, chương trình giữ các câu đã nhận và chỉ yêu cầu lại phần thiếu, giảm nhóm từ 80 xuống 20 rồi 5 câu. Sau tối đa 3 lượt vẫn thiếu sẽ báo lỗi; chỉ ghi file `.vi.srt` khi đã đủ câu.

Nếu máy đã có `preview.sh` được tạo qua `/api/sync_preview`, kiểm tra input/output trước khi chạy lệnh burn đã lưu:

```sh
cat preview.sh
./preview.sh --list  # Chỉ liệt kê đầu vào, không dịch/burn
./preview.sh
```

Script hoạt động từ bất kỳ thư mục gọi nào và giữ đường dẫn đầu ra đã lưu để tiếp tục cache. `preview.sh` có thể được ghi lại qua endpoint `/api/sync_preview`; hàng đợi Web UI gọi trực tiếp `translate.py`, không tự cập nhật script này theo video đang xem. Muốn chủ động chọn tập khác, dùng `translate.sh --input ... --output ... --mode burn`.

Chọn một bộ hoặc một tập:

```sh
./translate.sh --input /duong-dan/video
./translate.sh --input /duong-dan/video/ep-001.mp4 --mode soft
```

Kết quả mặc định: `outputs/<mã-bộ>/ep-001/outputs/ep-001.vi.srt`, kèm `.zh.srt`; chế độ soft/burn thêm video `.vi.softsub.mp4`/`.vi.burned.mp4`. Khi chỉ định một tập/bộ, thư mục tương đối tính từ đầu vào đó; dùng cùng lệnh để tiếp tục đúng cache.

Chạy tuần tự, bỏ qua tập/chế độ hoàn tất. SHA-256 phát hiện nguồn thay đổi và tạo lại cache của tập đó. Nếu đã có file `.vi.srt` không rỗng trong thư mục output của tập, chương trình dùng lại để xuất soft/burn mà không gọi API dịch hoặc chạy lại Whisper/OCR, kể cả khi thiếu dấu hoàn tất hay đổi model/OCR. File `.zh.srt` chỉ giúp bỏ qua nhận dạng, vẫn cần API để dịch sang tiếng Việt. Muốn dịch lại, xóa file `.vi.srt` và các đầu ra/dấu hoàn tất tương ứng của tập. Khi lỗi, giữ transcript tiếng Trung, dịch lại tập chưa hoàn tất ở lần sau; mã thoát 1 nếu có tập lỗi. Ctrl+C dừng, chạy lại cùng lệnh để tiếp tục. Không chạy hai lệnh vào cùng thư mục output. Chỉ xử lý MP4 đã tải xong; không đọc `.part`. Nên chờ downloader hoàn tất trước khi dịch.

Các video trong cùng nhóm phải có đường dẫn đầu ra riêng; tên chỉ khác chữ hoa/thường được coi là trùng để tránh ghi đè trên hệ thống không phân biệt hoa/thường.

Đây là dịch phụ đề, chưa có lồng tiếng. OCR/nhận dạng và cách xưng hô có thể sai, cần xem lại trước khi xuất bản. Chữ Trung đã in trên hình vẫn còn trong chế độ soft/burn.

### OCR vùng màn hình (tiện ích riêng)

```sh
./ocr.sh
```

`ocr.sh` mở `cr_region.py` bằng `uv`, cho phép chọn vùng màn hình rồi sao chép chữ nhận diện. Cần Python có Tkinter, Tesseract cài trong hệ thống với dữ liệu ngôn ngữ `vie` và `eng` (theo `LANG` trong source). Trên macOS, cho phép chụp màn hình khi hệ thống yêu cầu. Tiện ích này dùng Tesseract, độc lập với OCR video bằng RapidOCR trong Web UI; không có chế độ `--list`.

Kiểm tra offline:

```sh
uv run python -m unittest -v
node --test tests/*.cjs
node --check web/app.js
```

### Hàng đợi trong Web UI

Bấm **Chọn thư mục video…** để liệt kê MP4 trong thư mục và các thư mục con. Web UI ghi nhớ thư mục nguồn. Video có SRT tiếng Việt hoàn tất và SHA-256 khớp trong `outputs/` hoặc thư mục đầu ra hiện tại được đánh dấu **Đã sub**. Các video này vẫn có thể mở để sửa phụ đề và đưa vào hàng đợi Softsub hoặc Burn. Ở chế độ chỉ tạo SRT, **Chọn tất cả** chỉ lấy các video chưa có phụ đề Việt. Nếu không đánh dấu ô nào, hàng đợi chạy video đang xem khi video đó còn đủ điều kiện. Chọn chế độ xuất và thư mục đầu ra trước khi chạy.

Mỗi video tạo/dùng lại SRT rồi burn xong mới chuyển video kế tiếp. Video lỗi được đánh dấu riêng và hàng đợi tiếp tục; trạng thái tổng báo lỗi nếu có video lỗi. **Dừng** hủy video hiện tại và bỏ qua các video đang chờ. Tải lại trang vẫn xem được tiến độ hàng đợi đang chạy; khởi động lại server không tự tiếp tục hàng đợi.

Với nhiều video, các thư mục con tính từ thư mục cha chung được giữ trong output để tránh đè các tập trùng tên giữa nhiều bộ. Với một video, giữ cách lưu cũ `output/<tên-video>/outputs/...`. Chọn lại cùng nhóm video và cùng output để tiếp tục đúng cache. Danh sách/tiến độ tồn tại trong bộ nhớ server.

### Tổ chức mã nguồn

- `web_ui.py` phục vụ HTTP và OCR thử. `web_runner.py` điều phối hàng đợi, trạng thái và hủy tiến trình. Giao diện nằm trong `web/`.
- `translate.py` khám phá video, kiểm tra fingerprint, khóa output và quản lý cache. `engine.py` điều phối nhận diện, dịch và xuất kết quả cho một video hoặc SRT.
- `recognition.py` xử lý Whisper, OCR và timing. `gemini.py` xử lý API, retry và fallback. `subtitles.py` đọc/ghi SRT. `media.py` chạy FFmpeg và ffprobe cho các thao tác media dùng chung.

### Xem thử phụ đề

Tua tới vị trí cần kiểm tra rồi bấm **Xem thử phụ đề** để tạo clip 5 giây với SRT đang sửa. Clip dùng kiểu in chữ của chế độ Burn. Preview chỉ tạo clip tạm trên máy.

### Chỉnh chữ trực tiếp trên video

Khi đang xem video, bấm **Tải SRT của video đang xem** để mở trình sửa.
Chọn một câu trong danh sách để tua tới câu đó; bấm vào lớp phụ đề trên hình và gõ sửa.
Chữ cập nhật ngay vào SRT đang sửa. **+ Câu mới tại đây** thêm câu tại vị trí đang tua;
có thể sửa thời gian/nội dung trong bảng rồi **Áp dụng cho câu đang chọn**, hoặc xóa câu.
Lưu SRT trước khi xuất. SRT thô và transcript nguồn nằm trong mục nâng cao; câu trống
trong cache cũ không hiện trong danh sách chỉnh sửa.

Khi mở video, khung OCR hiện để bạn chỉnh. Bật **Sửa chữ trên video** để thao tác chữ;
thêm hoặc chọn vùng cũng chuyển sang thao tác vùng. Clip xem thử dùng để kiểm tra
kiểu chữ của bản Burn.
Chỉ sửa lớp phụ đề thêm vào, không biến chữ đã dính vào hình gốc thành văn bản có thể sửa.

Nút tải SRT tự tìm thêm trong các cache dưới `outputs/` khi nhóm nguồn/thư mục đang
chọn khác lần chạy trước. Chỉ dùng cache có `source.json` khớp SHA-256 của video hiện
tại; không ghép theo tên tập đơn thuần. UI hiển thị đường dẫn đã tìm thấy và đường dẫn
sẽ lưu. Nếu có nhiều bản khớp, lấy bản sửa gần nhất; bản cũ không bị ghi đè khi chỉ tải.
Nút **Xem thử phụ đề** cũng tự tải SRT nếu cần. **+ Câu mới tại đây**
có thể bắt đầu trực tiếp khi chưa tải: đọc SRT hiện có trước để giữ lại các câu cũ.
