import socket
import logging
import threading
import time
import numpy as np
import torch
from collections import defaultdict

from . import config
from . import ml_handler # Import module xử lý ML
from . import firebase_client # Import module Firebase để gửi thông báo
from . import token_storage # Import để lấy token (mặc dù firebase_client đã làm)

_stop_udp = threading.Event()
# Sử dụng defaultdict để tự động tạo buffer và thời gian cooldown cho IP mới
_audio_buffers = defaultdict(lambda: torch.tensor([], dtype=torch.float32))
_last_alert_times = defaultdict(float)
_buffer_lock = threading.Lock() # Lock để bảo vệ truy cập vào buffers và alert times

def _process_audio_data(data_bytes, client_address):
    """
    Xử lý dữ liệu audio nhận được từ một client (ESP32).
    Args:
        data_bytes (bytes): Dữ liệu byte thô nhận được qua UDP.
        client_address (tuple): Địa chỉ (ip, port) của client gửi dữ liệu.
    """
    global _audio_buffers, _last_alert_times, _buffer_lock

    client_ip = client_address[0]
    num_bytes_received = len(data_bytes)

    # Kiểm tra xem số byte nhận được có hợp lệ không
    if num_bytes_received == 0:
        # logging.debug(f"UDP Server: Nhận được gói tin rỗng từ {client_ip}. Bỏ qua.")
        return
    if num_bytes_received % config.AUDIO_BYTES_PER_SAMPLE != 0:
        logging.warning(f"UDP Server: Từ {client_ip}, nhận {num_bytes_received} bytes, "
                        f"không phải bội số của {config.AUDIO_BYTES_PER_SAMPLE} bytes/sample. Bỏ qua gói tin.")
        return

    num_samples = num_bytes_received // config.AUDIO_BYTES_PER_SAMPLE
    # logging.debug(f"UDP Server: Nhận {num_samples} mẫu từ {client_ip}")

    try:
        # 1. Giải mã bytes thành numpy array int32
        samples_np = np.frombuffer(data_bytes, dtype=config.AUDIO_NUMPY_DTYPE)

        # 2. Chuyển đổi sang tensor float và chuẩn hóa về [-1.0, 1.0]
        # Thực hiện trên CPU trước
        audio_tensor_float = torch.from_numpy(samples_np.astype(np.float32) / (2**31)).float()

        # 3. Thêm dữ liệu vào buffer của client tương ứng (có khóa để đảm bảo an toàn thread)
        with _buffer_lock:
            _audio_buffers[client_ip] = torch.cat((_audio_buffers[client_ip], audio_tensor_float))
            current_buffer = _audio_buffers[client_ip] # Lấy buffer hiện tại để xử lý

            # 4. Xử lý các chunk hoàn chỉnh trong buffer
            chunks_processed = 0
            while len(current_buffer) >= config.AUDIO_CHUNK_SAMPLES:
                # Lấy chunk đầu tiên để xử lý
                process_chunk = current_buffer[:config.AUDIO_CHUNK_SAMPLES]
                # Cập nhật lại buffer (loại bỏ chunk đã lấy)
                current_buffer = current_buffer[config.AUDIO_CHUNK_SAMPLES:]
                _audio_buffers[client_ip] = current_buffer # Lưu lại buffer còn lại
                chunks_processed += 1

                # Giải phóng lock trước khi gọi hàm dự đoán (có thể tốn thời gian)
                # Điều này cho phép các gói tin khác được nhận và thêm vào buffer trong khi đang dự đoán
                # Lưu ý: Cần đảm bảo hàm predict_scream là thread-safe hoặc không sửa đổi trạng thái chung quá nhiều
                # (Model đã load là read-only nên ổn, matplotlib có thể là vấn đề)
                _buffer_lock.release()
                try:
                    logging.debug(f"UDP Server: Xử lý chunk {chunks_processed} từ {client_ip} ({len(process_chunk)} mẫu)")
                    # 5. Gọi hàm dự đoán từ ml_handler
                    prediction, confidence = ml_handler.predict_scream(process_chunk)
                finally:
                    # Lấy lại lock sau khi dự đoán xong để tiếp tục vòng lặp while hoặc thoát
                     _buffer_lock.acquire()


                if prediction is not None:
                    logging.info(f"UDP Server: Dự đoán từ {client_ip} - Kết quả: {prediction} ({confidence*100:.1f}%)")

                    # 6. Kiểm tra nếu là tiếng hét và gửi thông báo (nếu không trong cooldown)
                    if prediction == 'Hét':
                        current_time = time.time()
                        last_alert_time = _last_alert_times[client_ip] # Lấy thời gian cảnh báo cuối cùng cho IP này

                        if current_time - last_alert_time > config.SCREAM_ALERT_COOLDOWN_S:
                            logging.warning(f"--- !!! Phát hiện tiếng hét từ {client_ip} !!! ---")
                            # Gửi thông báo FCM đến tất cả các thiết bị đã đăng ký
                            alert_body = config.SCREAM_ALERT_BODY_TEMPLATE.format(client_ip)
                            # Hàm này sẽ lấy danh sách token và gửi đi
                            success = firebase_client.send_alert_to_all(config.SCREAM_ALERT_TITLE, alert_body)
                            if success:
                                logging.info(f"UDP Server: Đã gửi cảnh báo tiếng hét từ {client_ip} đến các thiết bị.")
                                # Cập nhật thời gian cảnh báo cuối cùng cho IP này
                                _last_alert_times[client_ip] = current_time
                            else:
                                logging.error(f"UDP Server: Gửi cảnh báo tiếng hét từ {client_ip} thất bại.")
                        else:
                            logging.info(f"UDP Server: Phát hiện tiếng hét từ {client_ip} nhưng đang trong thời gian cooldown. Bỏ qua gửi thông báo.")
                else:
                    logging.warning(f"UDP Server: Dự đoán từ {client_ip} trả về None hoặc lỗi.")

            # Kết thúc vòng lặp while, buffer có thể còn dữ liệu chưa đủ chunk

    except Exception as e:
        logging.error(f"UDP Server: Lỗi nghiêm trọng khi xử lý dữ liệu từ {client_ip}: {e}", exc_info=True)
        # Cân nhắc xóa buffer của client này nếu lỗi liên tục
        with _buffer_lock:
            if client_ip in _audio_buffers:
                del _audio_buffers[client_ip]
            if client_ip in _last_alert_times:
                del _last_alert_times[client_ip]


def udp_listener():
    """Lắng nghe dữ liệu UDP từ các ESP32 và xử lý."""
    sock = None # Khởi tạo là None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((config.UDP_HOST, config.UDP_PORT))
        # Đặt timeout để vòng lặp không bị block mãi mãi, cho phép kiểm tra _stop_udp
        sock.settimeout(1.0) # Kiểm tra cờ dừng mỗi giây
        logging.info(f"UDP Server: Đang lắng nghe trên {config.UDP_HOST}:{config.UDP_PORT}...")

        while not _stop_udp.is_set():
            try:
                data, addr = sock.recvfrom(config.UDP_BUFFER_SIZE)
                if data:
                    # Gọi hàm xử lý dữ liệu trong một luồng riêng để không block luồng nhận chính?
                    # Hiện tại đang xử lý tuần tự trong luồng này. Nếu xử lý ML chậm, có thể làm mất gói tin UDP.
                    # Cân nhắc sử dụng ThreadPoolExecutor nếu cần xử lý song song.
                    # Tuy nhiên, cần cẩn thận với race conditions và tài nguyên (đặc biệt là GPU).
                    # Giữ đơn giản trước:
                    _process_audio_data(data, addr)

            except socket.timeout:
                # Timeout là bình thường, chỉ để kiểm tra cờ _stop_udp
                continue
            except OSError as e:
                # Có thể xảy ra nếu socket bị đóng đột ngột
                 logging.error(f"UDP Server: Lỗi socket OSError khi nhận dữ liệu: {e}", exc_info=True)
                 # Có thể cần break hoặc thử tạo lại socket nếu lỗi nghiêm trọng
                 time.sleep(1) # Chờ một chút trước khi thử lại
            except Exception as e:
                logging.error(f"UDP Server: Lỗi không xác định khi nhận hoặc xử lý dữ liệu: {e}", exc_info=True)
                # Ghi log lỗi nhưng tiếp tục chạy

    except OSError as e:
        logging.error(f"UDP Server: Lỗi khi bind UDP port {config.UDP_PORT}: {e}. Cổng có thể đang được sử dụng hoặc cần quyền.")
    except Exception as e:
        logging.error(f"UDP Server: Lỗi không xác định trong luồng listener chính: {e}", exc_info=True)
    finally:
        if sock:
            sock.close()
        logging.info("UDP Server: Luồng listener đã dừng và socket đã đóng.")

def start_udp_thread() -> threading.Thread:
    """Khởi tạo và bắt đầu luồng chạy UDP listener."""
    if not ml_handler._is_model_loaded:
         logging.warning("UDP Server: Model ML chưa được tải. UDP listener sẽ khởi chạy nhưng không thể dự đoán.")
         # Vẫn khởi chạy để có thể nhận dữ liệu, nhưng cần load model sau.

    _stop_udp.clear() # Reset cờ dừng
    # Reset trạng thái khi khởi động lại (quan trọng nếu server restart)
    with _buffer_lock:
        _audio_buffers.clear()
        _last_alert_times.clear()

    udp_thread = threading.Thread(target=udp_listener, name="UDPListenerThread", daemon=True)
    udp_thread.start()
    logging.info("UDP Server: Luồng listener đã được khởi tạo và bắt đầu.")
    return udp_thread

def stop_udp_listener():
    """Dừng UDP listener một cách an toàn."""
    logging.info("UDP Server: Yêu cầu dừng luồng listener...")
    _stop_udp.set()
    # Không cần join() ở đây vì luồng là daemon=True và có timeout,
    # nó sẽ tự thoát khi chương trình chính kết thúc hoặc sau timeout tiếp theo.
    # Việc đóng socket được xử lý trong khối finally của udp_listener.
