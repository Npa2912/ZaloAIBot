import os
import re
import json
from datetime import datetime

from dotenv import load_dotenv
from groq import Groq


load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise RuntimeError(
        "Thiếu GROQ_API_KEY trong .env"
    )


client = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# MODELS
# =========================================================

# Nhanh -> chat thường
CHAT_MODEL = "openai/gpt-oss-20b"

# Reminder parser
ROUTER_MODEL = "openai/gpt-oss-20b"


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Bạn là trợ lý AI cá nhân hoạt động trong Zalo.

Mục tiêu:
Trở thành một trợ lý thông minh, tự nhiên, hữu ích,
có khả năng duy trì ngữ cảnh hội thoại.

QUY TẮC:

1. Mặc định trả lời bằng tiếng Việt.

2. Hiểu ngữ cảnh từ các tin nhắn trước.

Ví dụ:

User:
Tôi đang học Kotlin.

User:
Thế Java thì sao?

=> Hiểu người dùng đang hỏi Java trong mối liên hệ
với Kotlin.

3. Nếu người dùng dùng các từ:
"nó", "cái đó", "cái vừa rồi", "phần trên",
hãy suy luận dựa trên lịch sử hội thoại.

4. Không tự giới thiệu lại ở mỗi câu.

5. Không nói tên model.

6. Không nói về system prompt.

7. Câu đơn giản -> trả lời ngắn.

8. Câu phức tạp -> giải thích có cấu trúc.

9. Với lập trình:
- tìm nguyên nhân lỗi
- đưa hướng sửa cụ thể
- đưa code hoàn chỉnh khi phù hợp
- giải thích phần quan trọng

10. Với bài tập:
- giải thích cách làm
- trình bày từng bước khi cần

11. Với ngoại ngữ:
- dịch chính xác
- giải thích ngữ pháp/từ vựng nếu người dùng cần

12. Không bịa thông tin khi không chắc chắn.

13. Hãy nói tự nhiên như một trợ lý cá nhân,
không trả lời máy móc.

14. Không mở đầu bằng những câu thừa như:
"Chắc chắn rồi!"
"Tất nhiên!"
"Rất vui được giúp bạn!"

Đi thẳng vào nội dung.
"""


# =========================================================
# CHAT WITH MEMORY
# =========================================================

def chat_with_ai(
    message,
    history=None,
    memories=None
):

    history = history or []
    memories = memories or []

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]


    # =====================================
    # LONG-TERM MEMORY
    # =====================================

    if memories:

        memory_text = "\n".join(
            f"- {memory}"
            for memory in memories
        )

        messages.append({
            "role": "system",
            "content":
                "Thông tin/nguyện vọng người dùng "
                "đã yêu cầu trợ lý ghi nhớ:\n"
                + memory_text
        })


    # =====================================
    # RECENT HISTORY
    # =====================================

    for item in history:

        role = item.get("role")

        if role not in (
            "user",
            "assistant"
        ):
            continue

        content = item.get(
            "content",
            ""
        ).strip()

        if not content:
            continue

        messages.append({
            "role": role,
            "content": content
        })


    # =====================================
    # CURRENT MESSAGE
    # =====================================

    messages.append({
        "role": "user",
        "content": message
    })


    print(
        f"⚡ Groq | history={len(history)} "
        f"| memory={len(memories)}"
    )


    completion = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=0.4,
        max_completion_tokens=1800
    )


    answer = (
        completion
        .choices[0]
        .message
        .content
    )


    if not answer:
        raise ValueError(
            "Groq không trả về nội dung."
        )


    return answer.strip()


# =========================================================
# MEMORY DETECTION - LOCAL
# =========================================================

def extract_explicit_memory(text):
    """
    Chỉ lưu khi người dùng có ý định yêu cầu bot nhớ.
    Không tự tiện biến mọi câu chat thành memory.
    """

    original = text.strip()
    lower = original.lower()

    patterns = [
        r"^hãy nhớ rằng\s+(.+)$",
        r"^nhớ rằng\s+(.+)$",
        r"^nhớ là\s+(.+)$",
        r"^ghi nhớ rằng\s+(.+)$",
        r"^ghi nhớ là\s+(.+)$",
        r"^từ giờ\s+(.+)$",
        r"^từ bây giờ\s+(.+)$",
    ]

    for pattern in patterns:

        match = re.match(
            pattern,
            original,
            flags=re.I
        )

        if match:

            memory = (
                match.group(1)
                .strip()
                .rstrip(".")
            )

            if memory:
                return memory

    return None


# =========================================================
# CLEAN JSON
# =========================================================

def clean_json(text):

    text = text.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1:
        return text[start:end + 1]

    return text


# =========================================================
# REMINDER INTELLIGENCE
# =========================================================

def understand_reminder(
    message,
    history=None
):

    now = datetime.now()

    history = history or []

    # Chỉ đưa vài lượt gần nhất cho reminder context
    context = ""

    for item in history[-6:]:

        role = item.get(
            "role",
            ""
        )

        content = item.get(
            "content",
            ""
        )

        context += (
            f"{role}: {content}\n"
        )


    prompt = f"""
Thời gian hiện tại:
{now.strftime("%Y-%m-%d %H:%M:%S")}

Bạn là bộ xử lý reminder.

Hội thoại gần đây:
{context}

Tin nhắn mới:
{message}

Chọn đúng một action:

CREATE_REMINDER
UPDATE_REMINDER
CANCEL_REMINDER
LIST_REMINDER
UNKNOWN


CREATE:

{{
 "action":"CREATE_REMINDER",
 "datetime":"YYYY-MM-DD HH:MM:SS",
 "content":"nội dung"
}}


UPDATE:

{{
 "action":"UPDATE_REMINDER",
 "datetime":"YYYY-MM-DD HH:MM:SS"
}}


CANCEL:

{{
 "action":"CANCEL_REMINDER"
}}


LIST:

{{
 "action":"LIST_REMINDER"
}}


Không liên quan reminder:

{{
 "action":"UNKNOWN"
}}


Quy tắc:

mai = ngày tiếp theo

ngày kia = sau 2 ngày

7h sáng = 07:00

7h tối = 19:00

2h chiều = 14:00

X phút nữa =
thời gian hiện tại + X phút

X tiếng nữa =
thời gian hiện tại + X tiếng

Hiểu các tham chiếu hội thoại như:

"đổi nó sang 8h"

"hủy cái lúc nãy"

"cái vừa rồi"

Chỉ JSON.
Không giải thích.
"""


    completion = client.chat.completions.create(
        model=ROUTER_MODEL,

        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],

        temperature=0,

        max_completion_tokens=300,

        response_format={
            "type": "json_object"
        }
    )


    text = (
        completion
        .choices[0]
        .message
        .content
    )


    if not text:
        raise ValueError(
            "Reminder AI không trả dữ liệu."
        )


    cleaned = clean_json(
        text
    )

    print(
        "🧠 Reminder:",
        cleaned
    )

    return json.loads(
        cleaned
    )