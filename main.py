# @title Import necessary libraries
import asyncio
import traceback
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from langsmith.integrations.otel import configure

from config import SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_ERROR_LOG_CHANNEL_ID
from services import (
    update_all_records,
    remove_duplicate_message,
    message_divider_run,
    infer_account,
    check_last_message_upload, update_cancel_transactions, link_receipt_to_payments, send_unlinked_receipts_to_slack
)
from handlers import handle_message

configure()






# Slack 앱 초기화
app = AsyncApp(token=SLACK_BOT_TOKEN)


# 5분마다 실행할 함수
async def run_agent_routine():
    await update_all_records()
    await remove_duplicate_message()
    await message_divider_run()
    await update_cancel_transactions()
    await infer_account(app)
    await link_receipt_to_payments()

async def check_once_per_day():
    try:
        await check_last_message_upload(app)
        await send_unlinked_receipts_to_slack(app)
    except Exception as e:
        await app.client.chat_postMessage(
            channel=SLACK_ERROR_LOG_CHANNEL_ID,
            text=traceback.format_exc()
        )



# 스케줄러 설정
scheduler = AsyncIOScheduler()
scheduler.add_job(
    run_agent_routine,
    'interval',
    minutes=15
)

scheduler.add_job(
    check_once_per_day,
    'cron',
    hour=14,
    minute=0
)



# 모든 이벤트 로깅
app.event("message")(handle_message)


async def main():
    # check_last_message_upload()
    await run_agent_routine()
    try:
        scheduler.start()
        handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
        await handler.start_async()
    except Exception as e:
        print(f"앱 시작 실패: {e}")

if __name__ == "__main__":
    asyncio.run(main())