# @title Import necessary libraries
import json
import traceback
import re

import dotenv
import asyncio
import os
from typing import List, Tuple, Optional
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sqlalchemy import Column, Integer, String, DateTime, select, Text, Boolean, Float
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import MetaData
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import datetime

from agents.account_chat_agent import account_chat_agent
from agents.account_classifier import account_classifier, AccountClassificationOutput
from agents.message_divider_agent import card_message_divider_agent, DividedMessageOutput, bank_message_divider_agent
from langsmith.integrations.otel import configure

dotenv.load_dotenv()
POSTGRESQL_DATABASE_DSN = os.getenv('POSTGRESQL_DATABASE_DSN')
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_APP_TOKEN = os.getenv('SLACK_APP_TOKEN')
SLACK_ACCOUNT_CHANNEL_ID = os.getenv('SLACK_ACCOUNT_CHANNEL_ID')
SLACK_ERROR_LOG_CHANNEL_ID = os.getenv('SLACK_ERROR_LOG_CHANNEL_ID')

APP_NAME = "account_app"
USER_ID = "modepick"

configure()

# Database setup
metadata = MetaData(schema="modepick_management_prod")
Base = declarative_base(metadata=metadata)

class 장부_결제문자(Base):
    __tablename__ = '장부_결제문자'

    mac_message_id = Column(Text, primary_key=True)
    message = Column(Text)
    장부에포함 = Column(Boolean)
    결제시간 = Column(DateTime)
    발신번호 = Column(Text)
    transaction_type = Column(Text)
    amount = Column(Integer)
    currency = Column(Text)
    거래상대 = Column(Text)
    발신자명 = Column(Text)
    거래목적 = Column(Text)
    계정과목_대 = Column(Text)
    계정과목_소 = Column(Text)

    account_reason = Column(Text)
    confidence = Column(Float)


async def get_database_session() -> AsyncSession:
    """DSN을 사용하여 PostgreSQL 데이터베이스에 연결"""
    dsn = POSTGRESQL_DATABASE_DSN
    if not dsn:
        raise ValueError("POSTGRESQL_DATABASE_DSN 환경변수가 설정되지 않았습니다")
    
    # Convert sync DSN to async DSN (postgresql -> postgresql+asyncpg)
    if dsn.startswith('postgresql://'):
        dsn = dsn.replace('postgresql://', 'postgresql+asyncpg://')
    elif dsn.startswith('postgres://'):
        dsn = dsn.replace('postgres://', 'postgresql+asyncpg://')
    
    engine = create_async_engine(dsn)
    AsyncSessionLocal = sessionmaker(
        class_=AsyncSession, autocommit=False, autoflush=False, expire_on_commit=False, bind=engine
    )
    return AsyncSessionLocal()

# 카드사 발신번호 리스트
CARD_SENDER_LIST = {
    '+8215888900': '삼성카드',
    '+82220008100': '삼성카드',
    '+8215447200': '신한카드',
    '+8215776200': '현대카드',
    '+8215881688': '국민카드',
    '+8215888100': '롯데카드',
    '+82269589000': '우리카드',
}

BANK_SENDER_LIST = {
    '+8215993333': '카카오뱅크',
    '+8215778000': '신한은행',
}
async def update_all_records():
    """모든 장부_결제문자 레코드를 업데이트하여 장부에포함을 True로 설정하고 카드사명을 추가"""
    db_session = await get_database_session()
    try:
        # 모든 레코드 조회
        stmt = select(장부_결제문자)
        result = await db_session.execute(stmt)
        rows = result.scalars().all()
        
        for row in rows:
            # 장부에포함을 True로 설정
            row.장부에포함 = True
            
            # 발신번호를 기반으로 카드사명 설정
            if row.발신번호 in CARD_SENDER_LIST:
                row.발신자명 = CARD_SENDER_LIST[row.발신번호]

            elif row.발신번호 in BANK_SENDER_LIST:
                row.발신자명 = BANK_SENDER_LIST[row.발신번호]
            else:
                print(f"없는 번호: {row.발신번호}")

        # 모든 변경사항 커밋
        await db_session.commit()
        print(f"총 {len(rows)}개의 레코드를 업데이트했습니다.")
        
    finally:
        await db_session.close()


async def check_missing_messages():
    """SJ_와 HJ_ 메시지 비교하여 누락된 메시지 찾기"""
    db_session = await get_database_session()
    try:
        # 2025-09-01 00:00:00 (KST 기준으로 저장된 시간)
        filter_date = datetime.datetime(2025, 9, 1, 0, 0, 0)
        
        # SJ_로 시작하는 레코드 조회
        sj_stmt = select(장부_결제문자).filter(
            장부_결제문자.mac_message_id.like('SJ_%'),
            장부_결제문자.결제시간 >= filter_date,
            장부_결제문자.발신번호 == '+8215776200'
        )
        sj_result = await db_session.execute(sj_stmt)
        sj_rows = sj_result.scalars().all()
        
        # HJ_로 시작하는 레코드 조회
        hj_stmt = select(장부_결제문자).filter(
            장부_결제문자.mac_message_id.like('HJ_%'),
            장부_결제문자.결제시간 >= filter_date,
            장부_결제문자.발신번호 == '+8215776200'
        )
        hj_result = await db_session.execute(hj_stmt)
        hj_rows = hj_result.scalars().all()
        
        # SJ_ 레코드에서 '고*지'가 포함된 메시지들 필터링
        sj_goji_rows = [row for row in sj_rows if '고*지' in row.message]
        
        # HJ_ 레코드에서 '고*지'가 포함된 메시지들 필터링
        hj_goji_rows = [row for row in hj_rows if '고*지' in row.message]
        
        # 양방향 비교를 위한 메시지 set 생성 (앞 47자)
        sj_messages = {row.message[:47] for row in sj_goji_rows}
        hj_messages = {row.message[:47] for row in hj_goji_rows}
        
        # SJ_에는 있지만 HJ_에 없는 메시지들 찾기 (앞 47자 비교)
        sj_missing_in_hj = []
        for sj_row in sj_goji_rows:
            if sj_row.message[:47] not in hj_messages:
                sj_missing_in_hj.append(sj_row)
        
        # HJ_에는 있지만 SJ_에 없는 메시지들 찾기 (앞 47자 비교)
        hj_missing_in_sj = []
        for hj_row in hj_goji_rows:
            if hj_row.message[:47] not in sj_messages:
                hj_missing_in_sj.append(hj_row)
        
        print(f"SJ_ 레코드 총 개수: {len(sj_rows)}")
        print(f"HJ_ 레코드 총 개수: {len(hj_rows)}")
        print(f"SJ_에서 '고*지' 포함 메시지 개수: {len(sj_goji_rows)}")
        print(f"HJ_에서 '고*지' 포함 메시지 개수: {len(hj_goji_rows)}")
        print(f"SJ_에만 있는 메시지 개수: {len(sj_missing_in_hj)}")
        print(f"HJ_에만 있는 메시지 개수: {len(hj_missing_in_sj)}")
        
        print("\n=== SJ_에만 있는 메시지들 ===")
        for missing_row in sj_missing_in_hj:
            print(f"ID: {missing_row.mac_message_id}")
            print(f"메시지: {missing_row.message}")
            print(f"결제시간: {missing_row.결제시간}")
            print("-" * 50)
            
        print("\n=== HJ_에만 있는 메시지들 ===")
        for missing_row in hj_missing_in_sj:
            print(f"ID: {missing_row.mac_message_id}")
            print(f"메시지: {missing_row.message}")
            print(f"결제시간: {missing_row.결제시간}")
            print("-" * 50)
            
    finally:
        await db_session.close()


async def remove_duplicate_message():
    """SJ_로 시작하고 발신번호가 현대카드이며 '고*지'가 포함된 메시지들의 transaction_type을 N으로 업데이트"""
    db_session = await get_database_session()
    try:
        # SJ_로 시작하고, 발신번호가 '+8215776200'이며, '고*지'가 포함된 레코드 조회
        stmt = select(장부_결제문자).filter(
            장부_결제문자.mac_message_id.like('SJ_%'),
            장부_결제문자.발신번호 == '+8215776200',
            장부_결제문자.message.like('%고*지%')
        )
        
        result = await db_session.execute(stmt)
        rows = result.scalars().all()
        
        # transaction_type을 'N'으로 업데이트
        updated_count = 0
        for row in rows:
            row.transaction_type = 'N'
            updated_count += 1
        
        # 모든 변경사항 커밋
        await db_session.commit()
        print(f"총 {updated_count}개의 SJ_ '고*지' 레코드의 transaction_type을 'N'으로 업데이트했습니다.")
        
        # 업데이트된 레코드들 출력
        for row in rows:
            print(f"메시지: {row.message}")
            
    finally:
        await db_session.close()


async def infer_account(_app: AsyncApp):
    """거래목적이 없는 승인 레코드들 처리하기"""
    db_session = await get_database_session()
    try:
        # 먼저 transaction_type이 N이 아니고 None도 아니며 confidence가 0.95 이상인 레코드 조회 (컨텍스트 용도)
        all_records_stmt = select(장부_결제문자).filter(
            장부_결제문자.transaction_type != 'N',
            장부_결제문자.transaction_type.is_not(None),
            장부_결제문자.confidence >= 0.90
        )
        all_records_result = await db_session.execute(all_records_stmt)
        all_records = all_records_result.scalars().all()
        
        # 처리할 대상 레코드들 조회 (거래목적이 없는 승인 레코드)
        target_stmt = select(장부_결제문자).filter(
            장부_결제문자.transaction_type.in_(['승인']),
            장부_결제문자.거래상대.is_not(None),
            장부_결제문자.거래목적.is_(None)
        ).order_by(장부_결제문자.결제시간.asc())
        
        target_result = await db_session.execute(target_stmt)
        target_rows = target_result.scalars().all()

        import re
        from difflib import SequenceMatcher
        from rapidfuzz import fuzz

        session_count = 0

        def similarity(a, b):
            """두 문자열의 유사도 계산"""
            return fuzz.ratio(a,b)

        async def process_single_row(row, session_id_suffix):
            """단일 row 처리 함수"""
            # 각 작업마다 독립적인 데이터베이스 세션 생성
            local_db_session = await get_database_session()
            
            try:
                # 현재 처리할 row를 새 세션에서 다시 조회
                local_row_stmt = select(장부_결제문자).filter(
                    장부_결제문자.mac_message_id == row.mac_message_id
                )
                local_row_result = await local_db_session.execute(local_row_stmt)
                local_row = local_row_result.scalars().first()
                
                if not local_row:
                    return None
                
                # 현재 row의 거래상대와 비슷한 거래상대들을 모든 레코드에서 찾기
                similar_records = []
                current_party = local_row.거래상대

                for record in all_records:
                    similarity_score = similarity(current_party, record.거래상대)
                    if record.거래상대 and similarity_score > 70:  # 80% 이상 유사
                        # 거래목적, 계정과목 정보가 있는 경우만 추가
                        if (record.거래목적 or record.계정과목_대 or record.계정과목_소 or record.account_reason):
                            similar_records.append(record)
                
                # agent에 넘길 컨텍스트 정보 구성 (거래목적, 계정과목_대, 계정과목_소가 유니크하며 confidence가 가장 높은 것만)
                combination_best = {}
                
                for similar in similar_records:
                    # 거래목적, 계정과목_대, 계정과목_소 조합을 키로 사용
                    combination_key = (similar.거래목적, similar.계정과목_대, similar.계정과목_소)
                    
                    # 해당 조합이 처음이거나, 더 높은 confidence를 가진 경우 업데이트
                    if (combination_key not in combination_best or 
                        (similar.confidence and similar.confidence > (combination_best[combination_key]["confidence"] or 0))):
                        combination_best[combination_key] = {
                            "거래상대": similar.거래상대,
                            "거래목적": similar.거래목적,
                            "계정과목_대": similar.계정과목_대,
                            "계정과목_소": similar.계정과목_소,
                            "account_reason": similar.account_reason,
                            "confidence": similar.confidence
                        }
                
                # confidence 순으로 정렬하여 context_info 구성
                context_info = sorted(combination_best.values(), 
                                    key=lambda x: x["confidence"] or 0, reverse=True)
                
                SESSION_ID = f"session_{session_id_suffix}"
                adk_session_service = InMemorySessionService()
                adk_session = await adk_session_service.create_session(app_name=APP_NAME, user_id=USER_ID,
                                                                       session_id=SESSION_ID)

                runner = Runner(
                    agent=account_classifier,
                    app_name=APP_NAME,
                    session_service=adk_session_service
                )
                
                # 컨텍스트 정보와 함께 메시지 구성
                party_str = f"거래상대: {local_row.거래상대}, 금액: {local_row.amount}{local_row.currency}"
                if context_info:
                    party_str += f"\n\n유사한 거래 이력:\n"
                    for i, ctx in enumerate(context_info[:30]):  # 최대 30개까지만
                        party_str += f"{i+1}. 거래상대: {ctx['거래상대']}, 거래목적: {ctx['거래목적']}, 계정과목(대): {ctx['계정과목_대']}, 계정과목(소): {ctx['계정과목_소']}, 사유: {ctx['account_reason']}\n"
                
                content = types.Content(role='user', parts=[types.Part(text=party_str)])
                final_response_text = None
                async for event in runner.run_async(user_id=USER_ID, session_id=SESSION_ID,
                                                    new_message=content):
                    if event.is_final_response():
                        if event.content and event.content.parts:
                            final_response_text = event.content.parts[0].text
                        elif event.actions and event.actions.escalate:
                            final_response_text = f"Agent escalated: {event.error_message or 'No specific message.'}"

                if final_response_text:
                    account_classification_output = AccountClassificationOutput.model_validate_json(final_response_text)
                    
                    # Slack으로 결과 전송 (데스크톱 최적화 - 컴팩트)
                    date_str = local_row.결제시간.strftime("%m/%d %H:%M") if local_row.결제시간 else "미확인"
                    slack_message = f"""{date_str} | {local_row.발신자명} | {local_row.거래상대} | {local_row.amount:,}{local_row.currency} → `{account_classification_output.business_purpose}` > `{account_classification_output.main_category}` > `{account_classification_output.sub_category}` | 아이디: {local_row.mac_message_id}"""

                    try:
                        await _app.client.chat_postMessage(
                            channel=SLACK_ACCOUNT_CHANNEL_ID,
                            text=slack_message
                        )
                    except Exception as e:
                        print(f"Slack 메시지 전송 실패: {e}")
                    
                    local_row.거래목적 = account_classification_output.business_purpose
                    local_row.계정과목_대 = account_classification_output.main_category
                    local_row.계정과목_소 = account_classification_output.sub_category
                    local_row.account_reason = account_classification_output.reason
                    local_row.confidence = account_classification_output.confidence
                    await local_db_session.commit()
                
                return local_row
                
            except Exception as e:
                print(f"에러 발생: {e}")
                await local_db_session.rollback()
                return None
            finally:
                await local_db_session.close()

        # 10개 단위로 배치 처리
        batch_size = 5
        total_processed = 0
        
        for i in range(0, len(target_rows), batch_size):
            batch = target_rows[i:i + batch_size]
            print(f"\n배치 {i//batch_size + 1} 처리 중... ({len(batch)}개 레코드)")
            
            # 배치 내 작업들을 병렬로 실행
            tasks = []
            for j, row in enumerate(batch):
                session_id = f"{total_processed + j}"
                tasks.append(process_single_row(row, session_id))
            
            # 배치 단위로 병렬 실행
            await asyncio.gather(*tasks)
            
            total_processed += len(batch)
            print(f"배치 완료. 총 {total_processed}개 처리됨")

            
    finally:
        await db_session.close()


async def preprocess_message(_message):
    new_message = _message
    new_message = new_message.replace("[Web발신] The Platinum ", "")
    new_message = new_message.replace("[Web발신] 올리브영 현대카드 ", "")
    new_message = new_message.replace("[Web발신] 대한항공카드 ", "")
    new_message = new_message.replace("[Web발신] KB국민카드", "")
    new_message = new_message.replace("[Web발신] [현대카드] ", "")
    new_message = new_message.replace("고*지", "")
    new_message = new_message.replace("권*진", "")
    new_message = new_message.replace("[Web발신]", "")
    new_message = new_message.replace(".00", "")
    new_message = new_message.split("누적")[0]
    new_message = new_message.split("잔액")[0]

    return new_message

async def message_divider_run():
    # results = await get_null_transaction_messages()
    # for mac_message_id, message, 결제시간, 발신번호 in results:
    #     print(f"{mac_message_id}: {message} {결제시간} {발신번호}")

    db_session = await get_database_session()
    try:
        # 2025-09-01 00:00:00 (KST 기준으로 저장된 시간)
        filter_date = datetime.datetime(2025, 9, 1, 0, 0, 0)

        stmt = select(장부_결제문자).filter(
            장부_결제문자.transaction_type.is_(None),
            장부_결제문자.결제시간 >= filter_date
        )

        result = await db_session.execute(stmt)
        rows = result.scalars().all()


        session_count = 0

        for row in rows:
            SESSION_ID = f"session_{session_count}"
            adk_session_service = InMemorySessionService()
            adk_session = await adk_session_service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)

            if row.발신번호 in CARD_SENDER_LIST:
                runner = Runner(
                    agent=card_message_divider_agent,
                    app_name=APP_NAME,
                    session_service=adk_session_service
                )
            elif row.발신번호 in BANK_SENDER_LIST:
                runner = Runner(
                    agent=bank_message_divider_agent,
                    app_name=APP_NAME,
                    session_service=adk_session_service
                )
            else:
                print(row.message, row.발신번호, "runner 생성 실패.")
                continue

            preprocessed_message = await preprocess_message(row.message)

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
                divided_message = DividedMessageOutput.model_validate_json(final_response_text)
                print(preprocessed_message)
                print(divided_message)
                print("-" * 50)
                row.transaction_type = divided_message.transaction_type
                row.amount = divided_message.amount
                row.currency = divided_message.currency
                row.거래상대 = divided_message.transaction_party
                await db_session.commit()

            session_count += 1

    finally:
        await db_session.close()

    # APP_NAME = "weather_app"
    # USER_ID = "1234"
    #
    #
    # messages = ["[Web발신] 신한(5126)해외승인 권*진 121,000 엔       (JP)08/30 16:24 JR EAST SH 누적3,098,169원", "[Web발신] The Platinum 승인  고*지  17,700원 일시불  06/20 11:50  (주)카카오모빌리티"]
    # session_count = 0
    # for message in messages:
    #     SESSION_ID = f"session_{session_count}"
    #     session_service = InMemorySessionService()
    #     session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)
    #     runner = Runner(
    #         agent=message_divider_agent,  # The agent we want to run
    #         app_name=APP_NAME,
    #         session_service=session_service
    #     )
    #
    #     content = types.Content(role='user', parts=[types.Part(text=message)])
    #
    #     async for event in runner.run_async(user_id=USER_ID, session_id=SESSION_ID,
    #                                         new_message=content):
    #         if event.is_final_response():
    #             if event.content and event.content.parts:
    #                 final_response_text = event.content.parts[0].text
    #             elif event.actions and event.actions.escalate:  # Handle potential errors/escalations
    #                 final_response_text = f"Agent escalated: {event.error_message or 'No specific message.'}"
    #
    #     session_count += 1
    #     print(f"{final_response_text}")


# if __name__ == "__main__":
#
#     # asyncio.run(update_all_records())
#     asyncio.run(count_unique_transaction_parties())
#     # asyncio.run(remove_duplicate_message())
#     # asyncio.run(message_divider_run())


# Slack 앱 초기화
app = AsyncApp(token=SLACK_BOT_TOKEN)


# 5분마다 실행할 함수
async def run_agent_routine():
    await update_all_records()
    await remove_duplicate_message()
    await message_divider_run()
    await infer_account(app)
    # print("send_periodic_message")
    # await app.client.chat_postMessage(
    #     channel=SLACK_ACCOUNT_CHANNEL_ID,  # 채널 ID
    #     text="5분마다 보내는 메시지입니다!"
    # )

async def check_last_message_upload():
    async def check_async():
        db_session = await get_database_session()
        try:
            # SJ로 시작하는 가장 최신 메시지 조회
            sj_stmt = select(장부_결제문자.결제시간).filter(
                장부_결제문자.mac_message_id.like('SJ_%')
            ).order_by(장부_결제문자.결제시간.desc()).limit(1)
            
            sj_result = await db_session.execute(sj_stmt)
            sj_latest = sj_result.scalar()
            
            # HJ로 시작하는 가장 최신 메시지 조회
            hj_stmt = select(장부_결제문자.결제시간).filter(
                장부_결제문자.mac_message_id.like('HJ_%')
            ).order_by(장부_결제문자.결제시간.desc()).limit(1)
            
            hj_result = await db_session.execute(hj_stmt)
            hj_latest = hj_result.scalar()

            current_time = datetime.datetime.now(datetime.timezone.utc)
            
            # SJ 체크 (48시간 이상 차이)
            if sj_latest:
                # timezone naive인 경우 UTC로 간주
                if sj_latest.tzinfo is None:
                    sj_latest = sj_latest.replace(tzinfo=datetime.timezone.utc)
                
                time_diff = current_time - sj_latest
                print(f"SJ 최신 메시지 시간: {sj_latest}, 현재 시간: {current_time}, 차이: {time_diff}")
                
                if time_diff >= datetime.timedelta(hours=48):
                    try:
                        await app.client.chat_postMessage(
                            channel=SLACK_ACCOUNT_CHANNEL_ID,
                            text="<@U061Q5EC7FS> 마지막 메세지 업로드가 48시간 지났습니다. 업로드 부탁드려요~"
                        )
                        print("SJ 48시간 경고 메시지 전송 완료")
                    except Exception as e:
                        raise e
                else:
                    print(f"SJ는 아직 48시간이 지나지 않음 (남은 시간: {datetime.timedelta(hours=48) - time_diff})")
            
            # HJ 체크 (48시간 이상 차이)
            if hj_latest:
                # timezone naive인 경우 UTC로 간주
                if hj_latest.tzinfo is None:
                    hj_latest = hj_latest.replace(tzinfo=datetime.timezone.utc)
                
                time_diff = current_time - hj_latest
                print(f"HJ 최신 메시지 시간: {hj_latest}, 현재 시간: {current_time}, 차이: {time_diff}")
                
                if time_diff >= datetime.timedelta(hours=48):
                    try:
                        await app.client.chat_postMessage(
                            channel=SLACK_ACCOUNT_CHANNEL_ID,
                            text="<@U061DQFDYEM> 마지막 메세지 업로드가 48시간 지났습니다. 업로드 부탁드려요~"
                        )
                        print("HJ 48시간 경고 메시지 전송 완료")
                    except Exception as e:
                        raise e
                else:
                    print(f"HJ는 아직 48시간이 지나지 않음 (남은 시간: {datetime.timedelta(hours=48) - time_diff})")
                        
        finally:
            await db_session.close()

    try:
        await check_async()
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
    check_last_message_upload,
    'cron',
    hour=14,
    minute=0
)



# 모든 이벤트 로깅
@app.event("message")
async def handle_message(event, say, client):
    print(f"=== 메시지 이벤트 수신 ===")
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

                    if id_match:
                        extracted_id = id_match.group(1)
                        print(f"추출된 ID: {extracted_id}")
                        
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

                    # row.transaction_type = divided_message.transaction_type
                    # row.amount = divided_message.amount
                    # row.currency = divided_message.currency
                    # row.거래상대 = divided_message.transaction_party
                    # await db_session.commit()

                # response = generate_response(user_message)

                # 같은 스레드에 답변
                # say(text=response, thread_ts=thread_ts)
    # else:
    #     # 일반 메시지에 답변
    #     response = generate_response(event["text"])
    #     say(response)

    # print(f"전체 이벤트: {event}")
    # print(f"채널 ID: {event.get('channel')}")
    # print(f"설정된 채널 ID: {SLACK_ACCOUNT_CHANNEL_ID}")
    #
    # # 봇이 보낸 메시지는 무시
    # if event.get("bot_id"):
    #     print("봇 메시지 무시")
    #     return
    #
    # # 특정 채널만 모니터링
    # if event.get("channel") == SLACK_ACCOUNT_CHANNEL_ID:
    #     user_message = event.get("text", "")
    #     print(f"처리할 메시지: {user_message}")
    #
    #     # 답변 전송
    #     try:
    #         await say(f"Echo: {user_message}")
    #         print("메시지 전송 완료")
    #     except Exception as e:
    #         print(f"메시지 전송 실패: {e}")
    # else:
    #     print("다른 채널의 메시지")


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