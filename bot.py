import os
import re
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
    get_user_reminders,
    get_pending_reminders,
    update_reminder,
    cancel_reminder,
    mark_done,
    save_message,
    get_recent_messages,
    add_memory,
    get_memories,
    clear_memories,
    clear_chat_history,
)

from ai_service import (
    chat_with_ai,
    understand_reminder,
    extract_explicit_memory,
)


# =========================================================
# ENV
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("ZALO_BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError(
        "❌ Thiếu ZALO_BOT_TOKEN trong Environment Variables hoặc .env"
    )


# =========================================================
# WEB SERVER CHO RENDER
# =========================================================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return {
        "status": "online",
        "service": "Zalo Personal AI"
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
        f"🌐 HTTP server chạy port {port}"
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
    chat_id,
    reminder_id,
    run_time,
    content
):

    try:

        seconds = (
            run_time - datetime.now()
        ).total_seconds()

        if seconds > 0:

            print(
                f"⏳ Reminder #{reminder_id} "
                f"còn {int(seconds)} giây"
            )

            await asyncio.sleep(
                seconds
            )

        # Chủ động gửi tin nhắn.
        # Không phụ thuộc update.message.
        await bot.send_message(
            chat_id=str(chat_id),
            text=f"🔔 Đến giờ {content} rồi!"
        )

        mark_done(
            reminder_id
        )

        running_tasks.pop(
            reminder_id,
            None
        )

        print(
            f"✅ Reminder #{reminder_id} "
            f"đã gửi: {content}"
        )

    except asyncio.CancelledError:

        print(
            f"🚫 Reminder #{reminder_id} "
            f"đã bị hủy"
        )

    except Exception as e:

        # Không mark DONE nếu gửi lỗi.
        # Lịch vẫn PENDING trong DB.
        print(
            f"❌ Reminder #{reminder_id}: {e}"
        )

        running_tasks.pop(
            reminder_id,
            None
        )


def schedule_reminder(
    bot,
    chat_id,
    reminder_id,
    run_time,
    content
):

    old_task = running_tasks.get(
        reminder_id
    )

    if old_task:

        old_task.cancel()

    task = asyncio.create_task(
        reminder_task(
            bot,
            chat_id,
            reminder_id,
            run_time,
            content
        )
    )

    running_tasks[
        reminder_id
    ] = task

    print(
        f"⏰ Scheduled #{reminder_id}: "
        f"{run_time.strftime('%d/%m/%Y %H:%M:%S')} "
        f"- {content}"
    )


# =========================================================
# RESTORE REMINDER
# =========================================================

async def restore_reminders(bot):

    reminders = (
        get_pending_reminders()
    )

    print()
    print("==============================")
    print(
        f"♻️ PENDING reminders: {len(reminders)}"
    )

    restored = 0
    missed = 0

    for (
        reminder_id,
        user_id,
        remind_at,
        content
    ) in reminders:

        try:

            run_time = (
                datetime.fromisoformat(
                    remind_at
                )
            )

            # ---------------------------------------------
            # ĐÃ QUÁ GIỜ
            # ---------------------------------------------

            if run_time <= datetime.now():

                try:

                    await bot.send_message(
                        chat_id=str(user_id),
                        text=(
                            "⚠️ Bạn có một lời nhắc bị lỡ "
                            "trong lúc bot offline:\n\n"
                            f"🔔 {content}\n"
                            f"⏰ {run_time.strftime('%d/%m/%Y %H:%M')}"
                        )
                    )

                    mark_done(
                        reminder_id
                    )

                    missed += 1

                except Exception as e:

                    print(
                        f"❌ Không gửi được reminder "
                        f"quá hạn #{reminder_id}: {e}"
                    )

                continue

            # ---------------------------------------------
            # CHƯA TỚI GIỜ
            # ---------------------------------------------

            schedule_reminder(
                bot,
                user_id,
                reminder_id,
                run_time,
                content
            )

            restored += 1

        except Exception as e:

            print(
                f"❌ Restore #{reminder_id}: {e}"
            )

    print(
        f"♻️ Restored: {restored}"
    )

    print(
        f"⚠️ Missed: {missed}"
    )

    print("==============================")
    print()


# =========================================================
# LOCAL ROUTER
# =========================================================

def local_router(text):

    lower = (
        text
        .lower()
        .strip()
    )

    # =====================================================
    # MEMORY
    # =====================================================

    memory_show = [
        "bạn nhớ gì về tôi",
        "m nhớ gì về t",
        "mày nhớ gì về tao",
        "nhớ gì về tôi",
        "nhớ gì về t",
        "xem bộ nhớ",
    ]

    if lower in memory_show:

        return {
            "action": "SHOW_MEMORY"
        }

    memory_clear = [
        "xóa bộ nhớ",
        "xoá bộ nhớ",
        "quên hết đi",
        "xóa những gì đã nhớ",
        "xoá những gì đã nhớ",
    ]

    if lower in memory_clear:

        return {
            "action": "CLEAR_MEMORY"
        }

    history_clear = [
        "xóa lịch sử chat",
        "xoá lịch sử chat",
        "reset cuộc trò chuyện",
        "reset chat",
    ]

    if lower in history_clear:

        return {
            "action": "CLEAR_HISTORY"
        }

    # =====================================================
    # XEM LỊCH
    # =====================================================

    list_patterns = [
        "xem lịch",
        "lịch của tôi",
        "lịch của t",
        "tôi có lịch gì",
        "t có lịch gì",
        "t có lịch j",
        "có lịch gì",
        "có lịch j",
        "mai t có lịch",
        "mai tôi có lịch",
        "mai có lịch",
    ]

    if any(
        p in lower
        for p in list_patterns
    ):

        return {
            "action": "LIST_REMINDER"
        }

    # =====================================================
    # HỦY
    # =====================================================

    if (
        (
            "hủy" in lower
            or "huỷ" in lower
        )
        and (
            "lịch" in lower
            or "nhắc" in lower
            or "cái vừa rồi" in lower
            or "nó" in lower
        )
    ):

        return {
            "action": "CANCEL_REMINDER"
        }

    # =====================================================
    # X PHÚT NỮA
    # =====================================================

    match = re.search(
        r"(\d+)\s*phút\s*nữa.*?"
        r"(?:nhắc|kêu|báo)\s*"
        r"(?:tôi|t|tao|mình)?\s*(.*)",
        lower,
        re.I
    )

    if match:

        minutes = int(
            match.group(1)
        )

        content = (
            match.group(2).strip()
            or "làm việc"
        )

        run_time = (
            datetime.now()
            + timedelta(
                minutes=minutes
            )
        )

        return {
            "action": "CREATE_REMINDER",
            "datetime": run_time,
            "content": content
        }

    # =====================================================
    # X GIỜ NỮA
    # =====================================================

    match = re.search(
        r"(\d+)\s*(?:tiếng|giờ)\s*nữa.*?"
        r"(?:nhắc|kêu|báo)\s*"
        r"(?:tôi|t|tao|mình)?\s*(.*)",
        lower,
        re.I
    )

    if match:

        hours = int(
            match.group(1)
        )

        content = (
            match.group(2).strip()
            or "làm việc"
        )

        run_time = (
            datetime.now()
            + timedelta(
                hours=hours
            )
        )

        return {
            "action": "CREATE_REMINDER",
            "datetime": run_time,
            "content": content
        }

    # =====================================================
    # REMINDER CẦN AI HIỂU
    # =====================================================

    reminder_words = [
        "nhắc",
        "lịch",
        "báo tôi",
        "báo t",
        "báo tao",
        "kêu tôi",
        "kêu t",
        "đổi lịch",
        "đổi lại",
        "sửa lịch",
    ]

    if any(
        word in lower
        for word in reminder_words
    ):

        return {
            "action": "AI_REMINDER"
        }

    return {
        "action": "CHAT"
    }


# =========================================================
# START COMMAND
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👋 Xin chào!\n\n"
        "Mình là trợ lý AI cá nhân của bạn.\n"
        "Bạn có thể:\n"
        "• Trò chuyện\n"
        "• Hỏi bài\n"
        "• Hỏi code\n"
        "• Đặt lịch nhắc\n"
        "• Xem lịch\n"
        "• Lưu thông tin cần nhớ"
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
        "♻️ Đã kiểm tra và khôi phục lịch."
    )


# =========================================================
# HANDLE MESSAGE
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
        update.message.text.strip()
    )

    user_id = str(
        update.effective_user.id
    )

    print()
    print("============================")
    print(
        f"👤 Người dùng: {text}"
    )

    # =====================================================
    # LOAD CONTEXT
    # =====================================================

    history = get_recent_messages(
        user_id,
        limit=16
    )

    memories = get_memories(
        user_id,
        limit=20
    )

    result = local_router(
        text
    )

    action = result.get(
        "action",
        "CHAT"
    )

    print(
        f"⚡ LOCAL: {action}"
    )

    # =====================================================
    # SHOW MEMORY
    # =====================================================

    if action == "SHOW_MEMORY":

        if not memories:

            await update.message.reply_text(
                "Hiện mình chưa lưu thông tin "
                "lâu dài nào về bạn."
            )

            return

        response = (
            "🧠 Những điều mình đang nhớ:\n\n"
        )

        for memory in memories:

            response += (
                f"• {memory}\n"
            )

        await update.message.reply_text(
            response
        )

        return

    # =====================================================
    # CLEAR MEMORY
    # =====================================================

    if action == "CLEAR_MEMORY":

        clear_memories(
            user_id
        )

        await update.message.reply_text(
            "🧹 Đã xóa bộ nhớ cá nhân."
        )

        return

    # =====================================================
    # CLEAR HISTORY
    # =====================================================

    if action == "CLEAR_HISTORY":

        clear_chat_history(
            user_id
        )

        await update.message.reply_text(
            "🧹 Đã reset lịch sử hội thoại."
        )

        return

    # =====================================================
    # AI REMINDER
    # =====================================================

    if action == "AI_REMINDER":

        try:

            print(
                "🧠 AI đang phân tích reminder..."
            )

            result = await asyncio.to_thread(
                understand_reminder,
                text,
                history
            )

            action = result.get(
                "action",
                "UNKNOWN"
            )

            print(
                f"🎯 AI ACTION: {action}"
            )

        except Exception as e:

            print(
                f"❌ Reminder AI: {e}"
            )

            await update.message.reply_text(
                "Mình chưa hiểu chính xác "
                "lịch bạn muốn đặt."
            )

            return

    # =====================================================
    # CREATE REMINDER
    # =====================================================

    if action == "CREATE_REMINDER":

        run_time = result.get(
            "datetime"
        )

        if isinstance(
            run_time,
            str
        ):

            parsed = None

            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ]

            for fmt in formats:

                try:

                    parsed = datetime.strptime(
                        run_time,
                        fmt
                    )

                    break

                except ValueError:

                    pass

            run_time = parsed

        content = (
            result.get(
                "content"
            )
            or "làm việc"
        )

        if not run_time:

            await update.message.reply_text(
                "Mình chưa hiểu chính xác thời gian."
            )

            return

        if run_time <= datetime.now():

            await update.message.reply_text(
                "⏰ Thời gian này đã qua rồi."
            )

            return

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

        return

    # =====================================================
    # UPDATE REMINDER
    # =====================================================

    if action == "UPDATE_REMINDER":

        reminder = get_last_reminder(
            user_id
        )

        if not reminder:

            await update.message.reply_text(
                "Bạn chưa có lịch nào để sửa."
            )

            return

        run_time = result.get(
            "datetime"
        )

        if isinstance(
            run_time,
            str
        ):

            parsed = None

            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ]

            for fmt in formats:

                try:

                    parsed = datetime.strptime(
                        run_time,
                        fmt
                    )

                    break

                except ValueError:

                    pass

            run_time = parsed

        if not run_time:

            await update.message.reply_text(
                "Mình chưa hiểu thời gian mới."
            )

            return

        if run_time <= datetime.now():

            await update.message.reply_text(
                "⏰ Thời gian mới đã qua."
            )

            return

        (
            reminder_id,
            _,
            content
        ) = reminder

        old_task = running_tasks.get(
            reminder_id
        )

        if old_task:

            old_task.cancel()

            running_tasks.pop(
                reminder_id,
                None
            )

        update_reminder(
            reminder_id,
            run_time.isoformat()
        )

        schedule_reminder(
            context.bot,
            user_id,
            reminder_id,
            run_time,
            content
        )

        answer = (
            f"🔄 Đã đổi lịch sang "
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

        return

    # =====================================================
    # CANCEL REMINDER
    # =====================================================

    if action == "CANCEL_REMINDER":

        reminder = get_last_reminder(
            user_id
        )

        if not reminder:

            await update.message.reply_text(
                "📭 Bạn không có lịch nào để hủy."
            )

            return

        (
            reminder_id,
            _,
            content
        ) = reminder

        task = running_tasks.get(
            reminder_id
        )

        if task:

            task.cancel()

            running_tasks.pop(
                reminder_id,
                None
            )

        cancel_reminder(
            reminder_id
        )

        answer = (
            f"🗑️ Đã hủy lịch: {content}"
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
    # LIST REMINDERS
    # =====================================================

    if action == "LIST_REMINDER":

        reminders = get_user_reminders(
            user_id
        )

        if not reminders:

            await update.message.reply_text(
                "📭 Bạn chưa có lịch nhắc nào."
            )

            return

        response = (
            "📅 Lịch hiện tại của bạn:\n\n"
        )

        for (
            reminder_id,
            remind_at,
            content
        ) in reminders:

            try:

                dt = datetime.fromisoformat(
                    remind_at
                )

                response += (
                    f"⏰ {dt.strftime('%d/%m %H:%M')}\n"
                    f"📝 {content}\n\n"
                )

            except Exception:

                pass

        await update.message.reply_text(
            response.strip()
        )

        return

    # =====================================================
    # NORMAL AI CHAT
    # =====================================================

    try:

        # ---------------------------------------------
        # EXPLICIT MEMORY
        # ---------------------------------------------

        new_memory = extract_explicit_memory(
            text
        )

        if new_memory:

            add_memory(
                user_id,
                new_memory
            )

            memories = get_memories(
                user_id,
                limit=20
            )

        print(
            "🤖 Gọi Groq..."
        )

        # ---------------------------------------------
        # AI
        # ---------------------------------------------

        answer = await asyncio.to_thread(
            chat_with_ai,
            text,
            history,
            memories
        )

        if not answer:

            answer = (
                "Mình chưa tạo được câu trả lời."
            )

        # ---------------------------------------------
        # SAVE HISTORY
        # ---------------------------------------------

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

        # ---------------------------------------------
        # ZALO MESSAGE LIMIT
        # ---------------------------------------------

        chunk_size = 2800

        for i in range(
            0,
            len(answer),
            chunk_size
        ):

            await update.message.reply_text(
                answer[
                    i:i + chunk_size
                ]
            )

    except Exception as e:

        print(
            f"❌ AI ERROR: {e}"
        )

        await update.message.reply_text(
            "⚠️ AI đang gặp lỗi tạm thời. "
            "Bạn gửi lại câu hỏi giúp mình nhé."
        )


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    # -----------------------------------------------------
    # HTTP SERVER CHO RENDER
    # -----------------------------------------------------

    web_thread = threading.Thread(
        target=run_web_server,
        daemon=True
    )

    web_thread.start()

    # -----------------------------------------------------
    # DATABASE
    # -----------------------------------------------------

    init_db()

    # -----------------------------------------------------
    # ZALO BOT
    # -----------------------------------------------------

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    # -----------------------------------------------------
    # HANDLERS
    # -----------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start
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
        "🤖 ZALO PERSONAL AI"
    )
    print(
        "⚡ Groq AI             : ON"
    )
    print(
        "🧠 Conversation Memory : ON"
    )
    print(
        "💾 SQLite              : ON"
    )
    print(
        "⏰ Reminder             : ON"
    )
    print(
        "🌐 Render HTTP Server  : ON"
    )
    print(
        "=========================================="
    )
    print()

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    app.run_polling()