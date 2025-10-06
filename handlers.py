import re
from sqlalchemy import select
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from database import get_database_session
from models import 장부_결제문자
from config import APP_NAME, USER_ID, SLACK_ACCOUNT_CHANNEL_ID
from agents.account_chat_agent import account_chat_agent
from agents.account_classifier import AccountClassificationOutput

async def handle_message(event, say, client):
    if event.get("bot_id"):
        return

    if "thread_ts" in event:
        thread_ts = event["thread_ts"]
        channel = event["channel"]

        if channel != SLACK_ACCOUNT_CHANNEL_ID:
            return

        # 스레드 원본 메시지 조회
        result = await client.conversations_history(
            channel=channel,
            latest=thread_ts,
            limit=1,
            inclusive=True
        )

        if result["messages"]:
            original_message = result["messages"][0]

            # 봇이 쓴 스레드인지 확인
            if original_message.get("bot_id") == event.get("app_id") or \
                    original_message.get("user") == (await client.auth_test())["user_id"]:
                user_message = event["text"]
                original_message_text = original_message['text']
                print(original_message_text)
                print(user_message)

                SESSION_ID = f"session_{thread_ts}"
                adk_session_service = InMemorySessionService()
                adk_session = await adk_session_service.create_session(app_name=APP_NAME, user_id=USER_ID,
                                                                       session_id=SESSION_ID)

                runner = Runner(
                    agent=account_chat_agent,
                    app_name=APP_NAME,
                    session_service=adk_session_service
                )

                preprocessed_message = f"""
                ## AGENT가 제공한 추론내용
                {original_message_text}
                
                ## 사용자가 채팅으로 수정 요청한 내용
                {user_message}
                """

                id_match = re.search(r'아이디:\s*([^\s]+)', original_message_text)
                extracted_id = None
                if id_match:
                    extracted_id = id_match.group(1)
                    print(f"추출된 ID: {extracted_id}")

                content = types.Content(role='user', parts=[types.Part(text=preprocessed_message)])
                final_response_text = None
                async for event in runner.run_async(user_id=USER_ID, session_id=SESSION_ID,
                                                    new_message=content):
                    if event.is_final_response():
                        if event.content and event.content.parts:
                            final_response_text = event.content.parts[0].text
                        elif event.actions and event.actions.escalate:  # Handle potential errors/escalations
                            final_response_text = f"Agent escalated: {event.error_message or 'No specific message.'}"

                if final_response_text:
                    account_classification_output = AccountClassificationOutput.model_validate_json(final_response_text)
                    print(account_classification_output)
                    print("-" * 50)

                    if extracted_id:
                        # 해당 ID로 데이터베이스에서 레코드 찾기
                        db_session = await get_database_session()
                        try:
                            target_stmt = select(장부_결제문자).filter(
                                장부_결제문자.mac_message_id == extracted_id
                            )
                            target_result = await db_session.execute(target_stmt)
                            target_row = target_result.scalars().first()
                            
                            if target_row:
                                # 분류 결과를 데이터베이스에 저장
                                target_row.거래목적 = account_classification_output.business_purpose
                                target_row.계정과목_대 = account_classification_output.main_category
                                target_row.계정과목_소 = account_classification_output.sub_category
                                target_row.account_reason = account_classification_output.reason
                                target_row.confidence = 1.0  # 사용자 수정이므로 신뢰도 1.0
                                
                                await db_session.commit()
                                print(f"ID {extracted_id}의 분류 정보가 업데이트되었습니다.")
                                
                                # 업데이트 완료 메시지를 스레드에 답변
                                await say(
                                    text=f"✅ `{extracted_id}` 분류 정보가 업데이트되었습니다!\n• 거래목적: `{account_classification_output.business_purpose}`\n• 계정과목(대): `{account_classification_output.main_category}`\n• 계정과목(소): `{account_classification_output.sub_category}`, reason: {target_row.account_reason}",
                                    thread_ts=thread_ts
                                )
                            else:
                                print(f"ID {extracted_id}에 해당하는 레코드를 찾을 수 없습니다.")
                                await say(
                                    text=f"❌ ID `{extracted_id}`에 해당하는 레코드를 찾을 수 없습니다.",
                                    thread_ts=thread_ts
                                )
                                
                        except Exception as e:
                            print(f"데이터베이스 업데이트 실패: {e}")
                            await say(
                                text=f"❌ 데이터베이스 업데이트 중 오류가 발생했습니다: {str(e)}",
                                thread_ts=thread_ts
                            )
                        finally:
                            await db_session.close()
                    else:
                        print("메시지에서 아이디를 찾을 수 없습니다.")
                        await say(
                            text="❌ 메시지에서 '아이디:' 부분을 찾을 수 없습니다. 올바른 형식으로 입력해주세요.",
                            thread_ts=thread_ts
                        )