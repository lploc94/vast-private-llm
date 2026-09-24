# Vast LLM riêng qua SSH

[English](README.md) · [Kiến trúc](docs/architecture.md) · [Triển khai và chi phí](docs/deployment.md) · [API](docs/api.md)

Ứng dụng Python chạy dashboard và API `/v1` trên máy của bạn. Khi deploy, ứng dụng thuê GPU Vast.ai, chạy vLLM trên instance và nối tới model qua SSH tunnel. Cổng HTTP của model không được mở công khai.

## Cài đặt và khởi động dashboard

Yêu cầu Python 3.12+, `uv` và OpenSSH. Tại thư mục project:

```sh
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Mở `http://127.0.0.1:8080`. Dashboard trên máy này không yêu cầu đăng nhập; trình duyệt được cấp một phiên nội bộ tự động. Các thao tác thay đổi dữ liệu vẫn cần CSRF token của phiên đó. API inference vẫn dùng Bearer key riêng. Giữ dashboard trên loopback; truy cập admin từ máy khác qua SSH forwarding để trình duyệt vẫn mở `localhost`.

Sao chép source từ `https://github.com/lploc94/vast-private-llm`; model weights được tải lên máy Vast lúc deploy, không nằm trong repo. Mã nguồn và tài liệu của project dùng [MIT License](LICENSE); model, vLLM, Vast.ai và dependencies có điều khoản riêng.

Đặt `VASTLLM_DATA_DIR` nếu muốn lưu SQLite và dữ liệu vận hành ở thư mục khác. Thư mục dữ liệu cần chỉ chủ máy đọc được. Mặc định dashboard và API chỉ nghe trên loopback.

## Luồng sử dụng

- **Deploy:** nhập Vast API key, tìm offer, thuê máy và theo dõi trạng thái.
- **API Keys:** cấp hoặc thu hồi key cho từng user. Key chỉ hiển thị một lần khi tạo.
- **Integration:** xem endpoint, method, curl dùng Bearer key và response mẫu; nút **Copy as Markdown** sao chép hướng dẫn để đưa cho agent tích hợp.
- **Inference:** ứng dụng cùng máy gọi `http://127.0.0.1:8080/v1` bằng Bearer key. Các request tới vLLM đi qua SSH tunnel.

Dashboard và API key hoạt động ngay cả khi chưa thuê máy. Trạng thái trong tab Deploy cho biết model đã sẵn sàng hay đang chờ khôi phục.

## API key và gọi model

Trong tab **API Keys**, nhập tên user, tạo key và sao chép giá trị được hiển thị một lần. Danh sách chỉ hiển thị phần đầu key, ngày tạo và trạng thái. Thu hồi key sẽ làm request tiếp theo bị từ chối. Key này chỉ gọi được `/v1`, không dùng cho dashboard.

Sau khi deployment báo `ready`, ứng dụng trên cùng máy có thể gọi:

```sh
curl http://127.0.0.1:8080/v1/models \
  -H 'Authorization: Bearer YOUR_USER_KEY'

curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_USER_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen-3.8","messages":[{"role":"user","content":"Xin chào"}],"max_tokens":16384}'
```

Response chat có dạng rút gọn: `{"model":"qwen-3.8","choices":[{"message":{"role":"assistant","content":"Xin chào!"},"finish_reason":"stop"}]}`. Nội dung thực tế phụ thuộc prompt và model.

Để nhận từng phần response, thêm `"stream":true` vào JSON của request chat. Khi chưa deploy, key hợp lệ nhận `503`; key thiếu, sai hoặc đã thu hồi nhận `401`. Mặc định API chỉ nghe trên `127.0.0.1`. Nếu app gọi từ máy khác, hãy kết nối máy qua mạng riêng/VPN trước khi mở bind ngoài loopback.

## Kết nối Vast và deploy

Tạo một Vast API key trong tài khoản Vast rồi dán vào phần **Vast API key** trong tab Deploy; app kiểm tra quyền truy cập Vast trước khi lưu vào `./data/vast_api_key` (quyền đọc chỉ dành cho chủ máy). Khi deploy, app tự tạo cặp SSH Ed25519 trong `<data-dir>/ssh/` và đăng ký public key với Vast **trước khi thuê máy**; bạn không phải tạo hay dán SSH key. Vast API key cần quyền `user_read` và `user_write` cho bước này. Dashboard chỉ hiển thị 4 ký tự đầu và 4 ký tự cuối để nhận diện key, không trả toàn bộ key. Cùng phần này hiển thị credit USD còn lại của tài khoản Vast mà key đang truy cập. Trình duyệt hiển thị ngay số liệu gần nhất (tối đa 24 giờ tuổi) kèm dòng “Cập nhật X giây trước”, rồi âm thầm lấy số mới từ Vast; làm mới mỗi phút khi tab Deploy mở hoặc khi bấm **Làm mới**. Nếu Vast tạm lỗi, số cũ vẫn hiển thị với ghi chú chưa làm mới được. Key lưu trong dashboard được ưu tiên; nếu chưa có, app dùng `VAST_API_KEY`, rồi tới key mà Vast CLI lưu ở `~/.config/vastai/vast_api_key`. Dashboard vẫn chạy được khi chưa có key, nhưng nút tìm offer và Deploy sẽ báo cấu hình còn thiếu. `VASTLLM_SSH_KEY_PATH` là tùy chọn để dùng private key SSH sẵn có; app không sửa key đó và vẫn tự đăng ký public key tương ứng. Nếu đang thuê instance, Vast API key thay thế phải truy cập được instance đó.

Tab **Deploy** tìm các offer on-demand phù hợp VRAM và disk bạn chọn cho Qwen3.8. Giá hiển thị là giá theo giờ tại thời điểm tìm; app kiểm tra lại offer trước khi thuê. Tiền thuê Vast bắt đầu khi instance được tạo, trong lúc model còn tải hoặc khởi động. Chọn máy có driver/CUDA tương thích và disk đủ cho model.

Khi deploy, dashboard hiển thị bước hiện tại và thời gian instance đã chạy theo `start_date` của Vast. Đồng hồ tiếp tục chạy qua các bước khởi động, nạp model và khi tải lại trang.

Model duy nhất là [`huihui-ai/Huihui-Qwen3.8-27B-abliterated`](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated), bản Qwen3.8-27B uncensored BF16. Mặc định tìm GPU từ **80 GB VRAM** và **120 GB disk** với CUDA >=13.0; 96 GB VRAM cho thêm khoảng trống vận hành. Checkpoint khoảng 55.6 GB trên disk; các ngưỡng này là cấu hình khởi đầu, chưa được kiểm chứng trên mọi instance Vast. App dùng image `vllm/vllm-openai:qwen38` để phục vụ text chat với context tối đa 262,144 token và tool calling. Prefix caching được bật khi khởi động vLLM để tái dùng phần đầu prompt trùng nhau và giảm thời gian xử lý input ở các lượt sau; cache nằm trong phiên chạy model, không lưu qua lần khởi động lại. vLLM chỉ chạy một lượt sinh token tại một thời điểm; request đến sau được xếp hàng. Dashboard không còn preset 0.6B hoặc ô model tùy chỉnh.

Tab Deploy hiển thị offer trong bảng, có thể sắp xếp theo tok/s ước lượng, giá/giờ hoặc tok/$ ước lượng. Tok/s cho một request đang decode được tính xấp xỉ `0,70 × gpu_mem_bw (GB/s) ÷ 55,563 GB trọng số BF16`; 55,563 GB lấy từ [recipe vLLM](https://github.com/vllm-project/recipes/blob/main/models/Qwen/Qwen3.8-27B.yaml), còn 0,70 là hệ số hiệu quả giả định. Tok/$ = `tok/s × 3600 ÷ giá/giờ`, giả định GPU sinh token liên tục. Đây là chỉ số so sánh từ băng thông Vast công bố, không phải benchmark hay cam kết tốc độ; nếu offer thiếu băng thông thì bảng hiển thị `—`. Sau khi thuê, đo tok/s thực tế bằng cùng prompt và số request dự kiến trước khi quyết định giữ máy.

Khi gọi API sau khi Qwen3.8 ready, trường `model` phải là `qwen-3.8` đúng như `/v1/models` trả về; tên Hugging Face dài chỉ dùng để deploy. Mỗi request chỉ hỗ trợ một lựa chọn (`n=1`). Có thể đặt `max_tokens` là 16,384 hoặc 32,768; tổng input và output không vượt 262,144 token. Khi dành 32,768 token cho output, còn tối đa 229,376 token input, kể cả system prompt và tool definitions. Nếu Vast không có offer đạt yêu cầu, tab Deploy báo không có máy và không tạo instance.

Vast chỉ mở SSH. vLLM nghe tại `127.0.0.1:8000` bên trong instance; dashboard tạo SSH tunnel tới cổng đó rồi mới bật `/v1` khi `/v1/models` trả đúng model. File `<data-dir>/known_hosts` do app quản lý dùng chính sách tin cậy host key ở kết nối đầu tiên; key thay đổi ở lần sau sẽ bị từ chối và hiện lỗi. Không xóa file này để bỏ qua cảnh báo host key mà chưa xác minh instance mới.

Mỗi lần bấm Deploy tạo một mã operation trước khi gọi Vast và đặt label `vastllm-<operation-id>` cho instance. Nếu tạo máy xong nhưng app mất response hoặc khởi động lại trước khi lưu instance ID, app tìm label trong danh sách instance của tài khoản. Khi chưa xác định được kết quả, app không thuê thêm máy; kiểm tra label và chi phí trong Vast Console trước khi xử lý thủ công.

## Vận hành, khôi phục và dừng chi phí

Chạy dashboard bằng trình quản lý tiến trình của máy bạn để app được khởi động lại sau reboot. Giữ bind `127.0.0.1`; dashboard không cấp phiên admin cho kết nối ngoài máy này. Sao lưu `app.db` khi app đã dừng hoặc dùng cơ chế backup SQLite; file chứa phiên, hash API key và operation/instance ID. Bảo vệ `data/vast_api_key`, `known_hosts` và SSH private key. Không chạy nhiều tiến trình app trên cùng thư mục dữ liệu.

Khi app khởi động lại, nó đọc deployment đã lưu, đối soát operation chưa có instance ID theo label, rồi kiểm tra lại Vast, SSH tunnel và model trước khi bật API. Nếu SSH hoặc model mất kết nối, `/v1` trả `503` cho key hợp lệ và dashboard hiển thị trạng thái offline/error. App thử nối lại với khoảng chờ có giới hạn; nút **Thử thiết lập lại** cho phép admin thử ngay mà không thuê thêm instance.

Nút **Destroy instance** yêu cầu xác nhận trước khi gọi Vast. Khi Vast xác nhận destroy, app tắt tunnel, xóa instance/operation khỏi trạng thái deployment và giữ các API key để dùng cho model deploy sau. Nếu Vast báo lỗi hoặc không phản hồi, app giữ instance ID để bạn thử lại và kiểm tra trực tiếp trên Vast Console. Tiền thuê chỉ dừng khi Vast xác nhận máy đã bị destroy; kiểm tra trang instance và hóa đơn Vast sau thao tác. Nếu create còn `create_unknown`, kiểm tra label `vastllm-<operation-id>` trên Vast trước khi destroy; app sẽ không thuê máy khác trong trạng thái này.
