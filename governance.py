# governance.py
# Mô-đun quản trị tuân thủ và kiểm duyệt rủi ro nội dung theo chuẩn BPM

def evaluate_content_risk(text):
    """
    Kiểm tra nội dung văn bản tự sinh để đánh giá mức độ rủi ro (Risk Scoring).
    Áp dụng lý thuyết quản trị tuân thủ trước khi tự động đăng bài.
    """
    if not text:
        return 0, "LOW"
    
    # Danh sách các từ khóa có nguy cơ vi phạm chính sách quảng cáo hoặc nhạy cảm cần kiểm duyệt
    sensitive_keywords = ["cam kết 100%", "chữa khỏi", "triệt để", "giá rẻ nhất", "siêu khuyến mãi sốc", "thuốc", "đặc trị"]
    
    score = 0
    for word in sensitive_keywords:
        if word in text.lower():
            score += 25
            
    # Phân loại mức độ rủi ro dựa trên điểm số
    if score >= 50:
        return score, "HIGH"
    elif score > 0:
        return score, "MEDIUM"
    else:
        return 0, "LOW"