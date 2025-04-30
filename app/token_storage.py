import threading
import logging

# Sử dụng set để tự động loại bỏ token trùng lặp và đảm bảo tính duy nhất
# Lưu ý: Đây là lưu trữ trong bộ nhớ, token sẽ mất khi server khởi động lại.
# Cần thay thế bằng cơ sở dữ liệu (SQLite, PostgreSQL, Redis,...) cho production.
_registered_tokens = set()
_token_lock = threading.Lock() # Lock để đảm bảo an toàn khi truy cập từ nhiều thread

def add_token(token: str) -> bool:
    """
    Thêm một FCM token mới vào bộ lưu trữ.
    Trả về True nếu token mới được thêm, False nếu token đã tồn tại.
    """
    if not isinstance(token, str) or not token:
        logging.warning(f"Cố gắng thêm token không hợp lệ: {token}")
        return False
    with _token_lock:
        if token not in _registered_tokens:
            _registered_tokens.add(token)
            logging.info(f"Token mới được thêm: {token[:10]}...")
            # TODO (Production): Thêm token vào cơ sở dữ liệu ở đây.
            return True
        else:
            # logging.debug(f"Token đã tồn tại trong bộ nhớ: {token[:10]}...")
            # TODO (Production): Có thể cập nhật timestamp hoặc thông tin khác trong DB nếu cần.
            return False

def remove_token(token: str):
    """Xóa một FCM token khỏi bộ lưu trữ."""
    with _token_lock:
        _registered_tokens.discard(token)
        logging.info(f"Token đã được xóa (nếu tồn tại): {token[:10]}...")
        # TODO (Production): Xóa token khỏi cơ sở dữ liệu ở đây.

def get_all_tokens() -> list[str]:
    """Lấy danh sách tất cả các token đang được lưu trữ."""
    with _token_lock:
        # Trả về một bản sao của danh sách để tránh thay đổi ngoài ý muốn
        return list(_registered_tokens)

