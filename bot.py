import os
import re
import json
import asyncio
import threading

from datetime import datetime, timedelta

from dotenv import load_dotenv
from flask import Flask

from zalo_bot import Update

from zalo_bot.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from database import (
    init_db,

    add_reminder,
    get_last_reminder,
    get_reminder_by_id,
    get_user_reminders,
    get_pending_reminders,
    update_reminder,
    cancel_reminder,
    mark_done,

    save_message,
    get_recent_messages,
    clear_chat_history,

    add_memory,
    get_memories,
    clear_memories,

    set_pending_state,
    get_pending_state,
    clear_pending_state,
)

from ai_service import (
    chat_with_ai,
    understand_reminder,
    extract_explicit_memory,
    BOT_NAME,
    DEVELOPER,
    COPYRIGHT,
)


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv(
    "ZALO_BOT_TOKEN"
)

if not BOT_TOKEN:
    raise RuntimeError(
        "❌ Thiếu ZALO_BOT_TOKEN."
    )


# =========================================================
# RENDER HTTP SERVER
# =========================================================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return {
        "status": "online",
        "service": BOT_NAME,
        "developer": DEVELOPER,
        "version": "2.0"
    }, 200


@web_app.route("/health")
def health():
    return "OK", 200


def run_web_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    print(
        f"🌐 HTTP server port {port}"
    )

    web_app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


# =========================================================
# REMINDER TASKS
# =========================================================

running_tasks = {}


async def reminder_task(
    bot,
    user_id,
    reminder_id,
    run_time,
    content
):

    try:

        delay = (
            run_time -
            datetime.now()
        ).total_seconds()

        if delay > 0:

            print(
                f"⏳ Reminder #{reminder_id}: "
                f"{int(delay)}s"
            )

            await asyncio.sleep(
                delay
            )

        await bot.send_message(
            chat_id=str(user_id),
            text=(
                f"🔔 Đến giờ {content} rồi!"
            )
        )

        mark_done(
            reminder_id
        )

        running_tasks.pop(
            reminder_id,
            None
        )

        print(
            f"✅ Reminder #{reminder_id} sent"
        )

    except asyncio.CancelledError:

        print(
            f"🚫 Reminder #{reminder_id} cancelled"
        )

    except Exception as e:

        running_tasks.pop(
            reminder_id,
            None
        )

        print(
            f"❌ Reminder #{reminder_id}: {e}"
        )


def schedule_reminder(
    bot,
    user_id,
    reminder_id,
    run_time,
    content
):

    old = running_tasks.get(
        reminder_id
    )

    if old:
        old.cancel()

    task = asyncio.create_task(
        reminder_task(
            bot,
            user_id,
            reminder_id,
            run_time,
            content
        )
    )

    running_tasks[
        reminder_id
    ] = task

    print(
        f"⏰ #{reminder_id} "
        f"{run_time.strftime('%d/%m/%Y %H:%M')} "
        f"{content}"
    )


# =========================================================
# RESTORE
# =========================================================

async def restore_reminders(bot):

    rows = get_pending_reminders()

    print(
        f"♻️ Restore {len(rows)} reminders"
    )

    for (
        reminder_id,
        user_id,
        remind_at,
        content
    ) in rows:

        try:

            run_time = datetime.fromisoformat(
                remind_at
            )

            if run_time <= datetime.now():

                await bot.send_message(
                    chat_id=str(user_id),
                    text=(
                        "⚠️ Bạn có lời nhắc bị lỡ:\n"
                        f"🔔 {content}\n"
                        f"⏰ {run_time.strftime('%d/%m %H:%M')}"
                    )
                )

                mark_done(
                    reminder_id
                )

                continue

            schedule_reminder(
                bot,
                user_id,
                reminder_id,
                run_time,
                content
            )

        except Exception as e:

            print(
                f"❌ Restore #{reminder_id}: {e}"
            )


# =========================================================
# HELPERS
# =========================================================

def parse_datetime(value):

    if isinstance(
        value,
        datetime
    ):
        return value

    if not value:
        return None

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M"
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                value,
                fmt
            )

        except ValueError:
            pass

    try:
        return datetime.fromisoformat(
            value
        )

    except Exception:
        return None


async def send_long_message(
    message,
    text
):

    if not text:
        return

    chunk_size = 2800

    for i in range(
        0,
        len(text),
        chunk_size
    ):

        await message.reply_text(
            text[
                i:i + chunk_size
            ]
        )


def looks_like_reminder(text):

    lower = text.lower()

    keywords = [
        "nhắc",
        "nhắc t",
        "nhắc tôi",
        "báo t",
        "báo tôi",
        "kêu t",
        "kêu tôi",
        "lịch",
        "hủy lịch",
        "huỷ lịch",
        "đổi lịch",
        "sửa lịch",
        "mai có gì",
        "mai t có gì",
        "mai tôi có gì",
    ]

    return any(
        x in lower
        for x in keywords
    )


# =========================================================
# FAST LOCAL REMINDER
# =========================================================

def fast_reminder(text):

    lower = text.lower().strip()

    # -----------------------------------------------------
    # X PHÚT NỮA
    # -----------------------------------------------------

    match = re.search(
        r"(\d+)\s*phút\s*nữa.*?"
        r"(?:nhắc|báo|kêu)\s*"
        r"(?:tôi|t|tao|mình)?\s*(.+)",
        lower,
        re.I
    )

    if match:

        minutes = int(
            match.group(1)
        )

        content = (
            match.group(2)
            .strip()
        )

        return {
            "action": "CREATE_REMINDER",
            "datetime": (
                datetime.now()
                + timedelta(
                    minutes=minutes
                )
            ),
            "content": content
        }

    # -----------------------------------------------------
    # X GIỜ NỮA
    # -----------------------------------------------------

    match = re.search(
        r"(\d+)\s*(?:giờ|tiếng)\s*nữa.*?"
        r"(?:nhắc|báo|kêu)\s*"
        r"(?:tôi|t|tao|mình)?\s*(.+)",
        lower,
        re.I
    )

    if match:

        hours = int(
            match.group(1)
        )

        content = (
            match.group(2)
            .strip()
        )

        return {
            "action": "CREATE_REMINDER",
            "datetime": (
                datetime.now()
                + timedelta(
                    hours=hours
                )
            ),
            "content": content
        }

    return None


# =========================================================
# ABOUT
# =========================================================

async def about(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        f"🤖 {BOT_NAME}\n\n"
        f"👨‍💻 Nhà phát triển: {DEVELOPER}\n"
        f"📱 Nền tảng: Zalo\n"
        f"🧠 Phiên bản: 2.0\n\n"
        f"{COPYRIGHT}"
    )

    await update.message.reply_text(
        text
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        f"👋 Mình là {BOT_NAME}.\n\n"
        "Bạn có thể trò chuyện, hỏi bài, hỏi code, "
        "đặt lịch nhắc hoặc yêu cầu mình ghi nhớ thông tin.\n\n"
        f"Phát triển bởi {DEVELOPER}."
    )


# =========================================================
# RESTORE COMMAND
# =========================================================

async def restore_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await restore_reminders(
        context.bot
    )

    await update.message.reply_text(
        "♻️ Đã kiểm tra lịch."
    )


# =========================================================
# REMINDER ACTION
# =========================================================

async def process_reminder_action(
    update,
    context,
    user_id,
    text,
    result
):

    action = result.get(
        "action"
    )

    # =====================================================
    # CLARIFY
    # =====================================================

    if action == "CLARIFY":

        question = result.get(
            "question"
        ) or (
            "Bạn nói rõ thêm giúp mình nhé."
        )

        state_data = {
            "original_message": text,
            "pending_action": result.get(
                "pending_action"
            ),
            "known_content": result.get(
                "known_content"
            )
        }

        set_pending_state(
            user_id,
            result.get(
                "pending_action",
                "REMINDER"
            ),
            json.dumps(
                state_data,
                ensure_ascii=False
            )
        )

        save_message(
            user_id,
            "user",
            text
        )

        save_message(
            user_id,
            "assistant",
            question
        )

        await update.message.reply_text(
            question
        )

        return True

    # =====================================================
    # CREATE
    # =====================================================

    if action == "CREATE_REMINDER":

        run_time = parse_datetime(
            result.get(
                "datetime"
            )
        )

        content = (
            result.get(
                "content"
            )
            or ""
        ).strip()

        if not content:

            question = (
                "Bạn muốn mình nhắc việc gì?"
            )

            set_pending_state(
                user_id,
                "CREATE_REMINDER",
                json.dumps({
                    "datetime": (
                        run_time.isoformat()
                        if run_time
                        else None
                    )
                })
            )

            await update.message.reply_text(
                question
            )

            return True

        if not run_time:

            question = (
                f"Bạn muốn mình nhắc '{content}' "
                "vào lúc nào?"
            )

            set_pending_state(
                user_id,
                "CREATE_REMINDER",
                json.dumps({
                    "content": content
                }, ensure_ascii=False)
            )

            await update.message.reply_text(
                question
            )

            return True

        if run_time <= datetime.now():

            await update.message.reply_text(
                "Thời gian đó đã qua rồi. "
                "Bạn muốn đặt vào thời gian nào?"
            )

            return True

        reminder_id = add_reminder(
            user_id,
            run_time.isoformat(),
            content
        )

        schedule_reminder(
            context.bot,
            user_id,
            reminder_id,
            run_time,
            content
        )

        clear_pending_state(
            user_id
        )

        answer = (
            f"⏰ Đã đặt lịch "
            f"{run_time.strftime('%d/%m lúc %H:%M')}.\n"
            f"📝 {content}"
        )

        save_message(
            user_id,
            "user",
            text
        )

        save_message(
            user_id,
            "assistant",
            answer
        )

        await update.message.reply_text(
            answer
        )

        return True

    # =====================================================
    # LIST
    # =====================================================

    if action == "LIST_REMINDER":

        reminders = get_user_reminders(
            user_id
        )

        if not reminders:

            answer = (
                "📭 Bạn chưa có lịch nhắc nào."
            )

        else:

            parts = [
                "📅 Lịch hiện tại:"
            ]

            for (
                rid,
                remind_at,
                content
            ) in reminders:

                try:

                    dt = datetime.fromisoformat(
                        remind_at
                    )

                    parts.append(
                        f"• {dt.strftime('%d/%m %H:%M')} — {content}"
                    )

                except Exception:
                    pass

            answer = "\n".join(
                parts
            )

        save_message(
            user_id,
            "user",
            text
        )

        save_message(
            user_id,
            "assistant",
            answer
        )

        await update.message.reply_text(
            answer
        )

        return True

    # =====================================================
    # CANCEL
    # =====================================================

    if action == "CANCEL_REMINDER":

        reminder_id = result.get(
            "reminder_id"
        )

        if reminder_id:

            reminder = get_reminder_by_id(
                reminder_id,
                user_id
            )

        else:

            reminder = None

        if not reminder:

            reminders = get_user_reminders(
                user_id
            )

            if len(reminders) == 1:

                rid, _, content = (
                    reminders[0]
                )

                reminder = {
                    "id": rid,
                    "content": content
                }

            elif len(reminders) == 0:

                await update.message.reply_text(
                    "Bạn không có lịch nào để hủy."
                )

                return True

            else:

                answer = (
                    "Bạn muốn hủy lịch nào?\n\n"
                )

                for (
                    rid,
                    remind_at,
                    content
                ) in reminders:

                    dt = datetime.fromisoformat(
                        remind_at
                    )

                    answer += (
                        f"• {dt.strftime('%d/%m %H:%M')} "
                        f"— {content}\n"
                    )

                set_pending_state(
                    user_id,
                    "CANCEL_REMINDER",
                    ""
                )

                await update.message.reply_text(
                    answer.strip()
                )

                return True

        rid = reminder["id"]

        task = running_tasks.get(
            rid
        )

        if task:

            task.cancel()

            running_tasks.pop(
                rid,
                None
            )

        cancel_reminder(
            rid
        )

        clear_pending_state(
            user_id
        )

        answer = (
            f"🗑️ Đã hủy lịch: "
            f"{reminder['content']}."
        )

        save_message(
            user_id,
            "user",
            text
        )

        save_message(
            user_id,
            "assistant",
            answer
        )

        await update.message.reply_text(
            answer
        )

        return True

    # =====================================================
    # UPDATE
    # =====================================================

    if action == "UPDATE_REMINDER":

        reminder_id = result.get(
            "reminder_id"
        )

        run_time = parse_datetime(
            result.get(
                "datetime"
            )
        )

        if not run_time:

            await update.message.reply_text(
                "Bạn muốn đổi lịch sang mấy giờ?"
            )

            set_pending_state(
                user_id,
                "UPDATE_REMINDER",
                json.dumps({
                    "reminder_id": reminder_id
                })
            )

            return True

        reminder = None

        if reminder_id:

            reminder = get_reminder_by_id(
                reminder_id,
                user_id
            )

        if not reminder:

            reminders = get_user_reminders(
                user_id
            )

            if len(reminders) == 1:

                rid, _, content = reminders[0]

                reminder = {
                    "id": rid,
                    "content": content
                }

            elif len(reminders) == 0:

                await update.message.reply_text(
                    "Bạn chưa có lịch nào để sửa."
                )

                return True

            else:

                await update.message.reply_text(
                    "Bạn có nhiều lịch. "
                    "Bạn muốn đổi lịch nào?"
                )

                set_pending_state(
                    user_id,
                    "UPDATE_REMINDER",
                    json.dumps({
                        "datetime": run_time.isoformat()
                    })
                )

                return True

        if run_time <= datetime.now():

            await update.message.reply_text(
                "Thời gian mới đã qua rồi. "
                "Bạn chọn thời gian khác nhé."
            )

            return True

        rid = reminder["id"]
        content = reminder["content"]

        task = running_tasks.get(
            rid
        )

        if task:

            task.cancel()

            running_tasks.pop(
                rid,
                None
            )

        update_reminder(
            rid,
            run_time.isoformat()
        )

        schedule_reminder(
            context.bot,
            user_id,
            rid,
            run_time,
            content
        )

        clear_pending_state(
            user_id
        )

        answer = (
            f"🔄 Đã đổi lịch '{content}' sang "
            f"{run_time.strftime('%d/%m lúc %H:%M')}."
        )

        save_message(
            user_id,
            "user",
            text
        )

        save_message(
            user_id,
            "assistant",
            answer
        )

        await update.message.reply_text(
            answer
        )

        return True

    return False


# =========================================================
# MESSAGE
# =========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.message.text:
        return

    text = (
        update.message.text
        .strip()
    )

    user_id = str(
        update.effective_user.id
    )

    print()
    print("==============================")
    print(
        f"👤 {user_id}: {text}"
    )

    history = get_recent_messages(
        user_id,
        limit=24
    )

    memories = get_memories(
        user_id,
        limit=30
    )

    pending = get_pending_state(
        user_id
    )

    reminders = get_user_reminders(
        user_id
    )

    # =====================================================
    # FIXED COMMANDS
    # =====================================================

    lower = text.lower()

    if lower in (
        "bạn nhớ gì về tôi",
        "m nhớ gì về t",
        "nhớ gì về tôi",
        "xem bộ nhớ"
    ):

        if not memories:

            await update.message.reply_text(
                "Hiện mình chưa lưu thông tin "
                "dài hạn nào về bạn."
            )

        else:

            answer = (
                "🧠 Những điều mình đang nhớ:\n\n"
                + "\n".join(
                    f"• {x}"
                    for x in memories
                )
            )

            await update.message.reply_text(
                answer
            )

        return

    if lower in (
        "xóa bộ nhớ",
        "xoá bộ nhớ",
        "quên hết đi"
    ):

        clear_memories(
            user_id
        )

        await update.message.reply_text(
            "🧹 Đã xóa bộ nhớ cá nhân."
        )

        return

    if lower in (
        "xóa lịch sử chat",
        "xoá lịch sử chat",
        "reset chat"
    ):

        clear_chat_history(
            user_id
        )

        clear_pending_state(
            user_id
        )

        await update.message.reply_text(
            "🧹 Đã reset ngữ cảnh hội thoại."
        )

        return

    # =====================================================
    # MEMORY SAVE
    # =====================================================

    memory = extract_explicit_memory(
        text
    )

    if memory:

        added = add_memory(
            user_id,
            memory
        )

        if added:

            answer = (
                f"🧠 Mình đã ghi nhớ: {memory}"
            )

        else:

            answer = (
                "🧠 Thông tin đó mình đã nhớ rồi."
            )

        save_message(
            user_id,
            "user",
            text
        )

        save_message(
            user_id,
            "assistant",
            answer
        )

        await update.message.reply_text(
            answer
        )

        return

    # =====================================================
    # FAST REMINDER
    # =====================================================

    fast = fast_reminder(
        text
    )

    if fast:

        await process_reminder_action(
            update,
            context,
            user_id,
            text,
            fast
        )

        return

    # =====================================================
    # REMINDER AI
    #
    # Nếu đang pending thì dù user chỉ nói "7h",
    # vẫn phải cho router xử lý.
    # =====================================================

    if (
        pending
        or looks_like_reminder(text)
    ):

        try:

            result = await asyncio.to_thread(
                understand_reminder,
                text,
                history,
                pending,
                reminders
            )

            action = result.get(
                "action"
            )

            if action != "NOT_REMINDER":

                handled = (
                    await process_reminder_action(
                        update,
                        context,
                        user_id,
                        text,
                        result
                    )
                )

                if handled:
                    return

        except Exception as e:

            print(
                f"❌ Reminder Router: {e}"
            )

            # Không làm bot chết.
            # Nếu router lỗi thì cho chat AI xử lý tiếp.

    # =====================================================
    # NORMAL AI CHAT
    # =====================================================

    try:

        print(
            "🤖 Smart chat..."
        )

        answer = await asyncio.to_thread(
            chat_with_ai,
            text,
            history,
            memories
        )

        save_message(
            user_id,
            "user",
            text
        )

        save_message(
            user_id,
            "assistant",
            answer
        )

        await send_long_message(
            update.message,
            answer
        )

    except Exception as e:

        print(
            f"❌ CHAT ERROR: {e}"
        )

        await update.message.reply_text(
            "Mình gặp lỗi khi xử lý câu này. "
            "Bạn gửi lại giúp mình nhé."
        )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    # HTTP server Render
    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    # Database
    init_db()

    # Zalo
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "about",
            about
        )
    )

    app.add_handler(
        CommandHandler(
            "restore",
            restore_command
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_message
        )
    )

    print()
    print(
        "=========================================="
    )
    print(
        f"🤖 {BOT_NAME} v2.0"
    )
    print(
        f"👨‍💻 Developer: {DEVELOPER}"
    )
    print(
        "🧠 Smart Context        : ON"
    )
    print(
        "🧠 Long-term Memory     : ON"
    )
    print(
        "❓ Clarification        : ON"
    )
    print(
        "⏰ Smart Reminder       : ON"
    )
    print(
        "⚡ Groq                 : ON"
    )
    print(
        "🌐 Render               : ON"
    )
    print(
        "=========================================="
    )
    print(
        COPYRIGHT
    )
    print()

    app.run_polling()