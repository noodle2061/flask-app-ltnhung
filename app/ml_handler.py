import logging
import os
import io
import time # Thêm import time

# --- THÊM VÀO ĐÂY ---
# Chỉ định backend không tương tác cho Matplotlib TRƯỚC khi import pyplot
import matplotlib
matplotlib.use('Agg') # Sử dụng backend 'Agg' để tránh lỗi GUI trong thread phụ
# --------------------

import torch
import torchaudio
import torchvision # Cần import torchvision để load model ResNet
from torchvision import transforms
from torchvision.models import resnet34 # Import cụ thể ResNet nếu cần khi load
import numpy as np
from PIL import Image
import matplotlib.pyplot as plt # Sử dụng matplotlib để tạo ảnh spectrogram

from . import config # Import config của app

# Biến toàn cục cho model và các thành phần xử lý (tránh load lại liên tục)
_model = None
_mel_transform = None
_transform_pipeline = None
_is_model_loaded = False

def load_model():
    """
    Tải model PyTorch và khởi tạo các thành phần xử lý.
    Trả về True nếu thành công, False nếu thất bại.
    """
    global _model, _mel_transform, _transform_pipeline, _is_model_loaded

    if _is_model_loaded:
        logging.info("ML Handler: Model đã được tải trước đó.")
        return True

    if not os.path.exists(config.MODEL_PATH):
        logging.error(f"ML Handler: Không tìm thấy tệp model tại '{config.MODEL_PATH}'")
        return False

    try:
        logging.info(f"ML Handler: Đang tải model từ {config.MODEL_PATH}...")
        # Quan trọng: Chỉ sử dụng weights_only=False nếu bạn tin tưởng nguồn gốc của tệp .pt
        # Nếu model được lưu bằng torch.save(model.state_dict(), ...), bạn cần tạo instance model trước rồi load state_dict
        # Nếu model được lưu bằng torch.save(model, ...), bạn có thể load trực tiếp như dưới đây
        # Giả sử model được lưu toàn bộ:
        _model = torch.load(config.MODEL_PATH, map_location=config.ML_DEVICE, weights_only=False) # weights_only=False có thể không an toàn

        # Nếu bạn lưu state_dict, hãy làm như sau:
        # _model = resnet34(weights=None) # Tạo instance ResNet, weights=None vì bạn sẽ load weights của mình
        # # Điều chỉnh lớp cuối cùng của ResNet nếu cần (ví dụ: nếu số lớp output khác ImageNet)
        # num_ftrs = _model.fc.in_features
        # _model.fc = torch.nn.Linear(num_ftrs, len(config.MODEL_CLASS_MAP)) # Số lớp output = số lớp trong map
        # _model.load_state_dict(torch.load(config.MODEL_PATH, map_location=config.ML_DEVICE))

        _model.to(config.ML_DEVICE) # Chuyển model đến thiết bị (CPU/GPU)
        _model.eval() # Chuyển model sang chế độ đánh giá (quan trọng!)
        logging.info(f"ML Handler: Model '{config.MODEL_FILENAME}' đã tải thành công và chuyển đến {config.ML_DEVICE}.")

        # Khởi tạo các thành phần transform một lần
        logging.info("ML Handler: Khởi tạo các phép biến đổi...")
        _mel_transform = torchaudio.transforms.MelSpectrogram(
            sample_rate=config.AUDIO_SAMPLE_RATE,
            n_mels=config.MODEL_N_MELS,
            n_fft=config.MODEL_N_FFT
        ).to(config.ML_DEVICE)

        _transform_pipeline = transforms.Compose([
            transforms.Resize(config.MODEL_IMG_SIZE),
            transforms.ToTensor(),
            # Bỏ comment nếu model của bạn cần chuẩn hóa ImageNet
            # transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])
        logging.info("ML Handler: Các phép biến đổi đã được khởi tạo.")

        _is_model_loaded = True
        return True

    except ImportError as e:
         logging.error(f"ML Handler: Lỗi import khi tải model. Đảm bảo torchvision đã được cài đặt đúng cách. Lỗi: {e}", exc_info=True)
         _model = None
         _is_model_loaded = False
         return False
    except Exception as e:
        logging.error(f"ML Handler: Lỗi không xác định khi tải model hoặc khởi tạo transform: {e}", exc_info=True)
        _model = None
        _is_model_loaded = False
        return False

def _pad_waveform(waveform, target_length):
    """Pads hoặc cắt bớt tensor waveform đến target_length."""
    # Đảm bảo waveform là 2D (channels, time)
    if waveform.ndim == 1:
        waveform = waveform.unsqueeze(0)

    num_channels, current_length = waveform.shape
    if current_length < target_length:
        padding = target_length - current_length
        waveform = torch.nn.functional.pad(waveform, (0, padding)) # Pad ở cuối
    elif current_length > target_length:
        waveform = waveform[:, :target_length] # Cắt từ cuối
    return waveform

def _audio_chunk_to_image_tensor(audio_chunk_tensor):
    """Biến đổi một đoạn audio tensor thành image tensor cho model."""
    global _mel_transform, _transform_pipeline

    if not _is_model_loaded or _mel_transform is None or _transform_pipeline is None:
        logging.error("ML Handler: Model hoặc transforms chưa được tải, không thể xử lý audio.")
        return None # Trả về None nếu chưa sẵn sàng

    try:
        # 1. Đảm bảo tensor audio ở đúng định dạng và thiết bị
        if audio_chunk_tensor.ndim == 1:
            audio_chunk_tensor = audio_chunk_tensor.unsqueeze(0) # Thêm chiều channel nếu thiếu
        audio_chunk_device = audio_chunk_tensor.to(config.ML_DEVICE) # Chuyển lên device

        # 2. Pad/Truncate waveform đến độ dài mong đợi khi huấn luyện
        audio_padded = _pad_waveform(audio_chunk_device, config.MODEL_TARGET_LENGTH_SAMPLES)

        # 3. Tạo Mel Spectrogram
        spectrogram = _mel_transform(audio_padded) # audio_padded đã ở trên device
        spectrogram = spectrogram + 1e-10 # Thêm epsilon nhỏ tránh log(0)

        # 4. Chuyển sang thang Log
        log_spectrogram = spectrogram.log2()

        # 5. Chuẩn hóa và chuyển đổi sang ảnh PIL dùng matplotlib (theo code mẫu)
        # Chuyển về CPU để xử lý numpy và matplotlib
        spec_np = log_spectrogram.squeeze().cpu().numpy()

        # Chuẩn hóa 0-1 (tùy chọn nhưng thường tốt)
        spec_min, spec_max = spec_np.min(), spec_np.max()
        if spec_max > spec_min:
            spec_norm = (spec_np - spec_min) / (spec_max - spec_min)
        else:
            spec_norm = np.zeros_like(spec_np)

        # Tạo ảnh từ matplotlib để có colormap 'viridis'
        # Tạo figure và axes mới mỗi lần để tránh vấn đề thread-safety của matplotlib
        # Dòng này sẽ không còn gây warning vì đã set backend 'Agg'
        fig, ax = plt.subplots(1, figsize=(config.MODEL_IMG_SIZE[1]/100, config.MODEL_IMG_SIZE[0]/100), dpi=100)
        fig.subplots_adjust(left=0, right=1, bottom=0, top=1) # Bỏ viền trắng
        ax.axis('off') # Tắt trục
        ax.imshow(spec_norm, cmap='viridis', aspect='auto', origin='lower')

        buf = io.BytesIO()
        try:
            # Lưu ảnh vào buffer trong bộ nhớ
            plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0, dpi=100)
        except Exception as save_err:
            logging.error(f"ML Handler: Lỗi khi lưu ảnh matplotlib: {save_err}", exc_info=True)
            return None # Trả về None nếu lỗi
        finally:
            plt.close(fig) # Luôn đóng figure để giải phóng bộ nhớ
        buf.seek(0)

        # Mở ảnh từ buffer bằng PIL và chuyển sang RGB
        try:
            img = Image.open(buf).convert('RGB')
        except Exception as img_err:
            logging.error(f"ML Handler: Lỗi khi mở ảnh từ buffer: {img_err}", exc_info=True)
            return None

        # 6. Áp dụng pipeline transform (Resize, ToTensor, Normalize nếu cần)
        img_tensor = _transform_pipeline(img)

        # 7. Thêm chiều batch (batch size = 1)
        img_tensor = img_tensor.unsqueeze(0)

        # 8. Đảm bảo tensor cuối cùng ở đúng device
        return img_tensor.to(config.ML_DEVICE)

    except Exception as e:
        logging.error(f"ML Handler: Lỗi trong quá trình chuyển đổi audio sang ảnh: {e}", exc_info=True)
        return None # Trả về None nếu có lỗi

def predict_scream(audio_chunk_tensor):
    """
    Thực hiện dự đoán tiếng hét từ một đoạn audio tensor.
    Args:
        audio_chunk_tensor (torch.Tensor): Tensor chứa dữ liệu audio float [-1.0, 1.0].
    Returns:
        tuple: (prediction_label, confidence) hoặc (None, 0.0) nếu lỗi hoặc không phát hiện.
               prediction_label (str): Tên lớp dự đoán ('Hét', 'Không hét', 'Unknown').
               confidence (float): Độ tin cậy của dự đoán (0.0 đến 1.0).
    """
    global _model

    if not _is_model_loaded or _model is None:
        logging.warning("ML Handler: Model chưa được tải, không thể dự đoán.")
        return None, 0.0

    start_time = time.time()

    # 1. Chuyển đổi audio thành image tensor
    image_tensor = _audio_chunk_to_image_tensor(audio_chunk_tensor)
    if image_tensor is None or image_tensor.nelement() == 0:
        logging.error("ML Handler: Không thể tạo image tensor từ audio chunk.")
        return None, 0.0

    # 2. Thực hiện dự đoán
    try:
        with torch.no_grad(): # Quan trọng: không tính gradient khi inference
            outputs = _model(image_tensor) # image_tensor đã ở trên device
            probabilities = torch.softmax(outputs, dim=1)
            confidence_tensor, predicted_idx_tensor = torch.max(probabilities, 1)

            # Chuyển kết quả về CPU để lấy giá trị
            predicted_idx = predicted_idx_tensor.cpu().item()
            confidence = confidence_tensor.cpu().item()

            prediction_label = config.MODEL_CLASS_MAP.get(predicted_idx, "Unknown")

        end_time = time.time()
        processing_time = end_time - start_time
        # Log này vẫn giữ nguyên, bạn có thể điều chỉnh nếu muốn
        logging.debug(f"ML Handler: Dự đoán hoàn tất trong {processing_time:.4f}s - Kết quả: {prediction_label} ({confidence*100:.1f}%)")

        # Chỉ trả về kết quả nếu là 'Hét' hoặc 'Không hét' (có thể tùy chỉnh)
        if prediction_label in config.MODEL_CLASS_MAP.values():
             return prediction_label, confidence
        else:
             logging.warning(f"ML Handler: Lớp dự đoán không xác định: index {predicted_idx}")
             return "Unknown", confidence # Hoặc trả về None, 0.0 tùy logic mong muốn

    except Exception as e:
        logging.error(f"ML Handler: Lỗi trong quá trình dự đoán: {e}", exc_info=True)
        return None, 0.0

# Tùy chọn: Gọi load_model() ngay khi import module này?
# Điều này có thể làm chậm quá trình khởi động server ban đầu.
# Cách tốt hơn là gọi nó từ app/__init__.py sau khi Flask app được tạo.
# load_model()
