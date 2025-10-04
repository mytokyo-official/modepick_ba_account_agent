# @title Import necessary libraries
import json

import dotenv
import asyncio
import os
from typing import List, Tuple, Optional


from sqlalchemy import Column, Integer, String, DateTime, select, Text, Boolean
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import MetaData
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import datetime

from agents.message_divider_agent import card_message_divider_agent, DividedMessageOutput, bank_message_divider_agent
from langsmith.integrations.otel import configure

dotenv.load_dotenv()

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

async def get_database_session() -> AsyncSession:
    """DSN을 사용하여 PostgreSQL 데이터베이스에 연결"""
    dsn = os.getenv('POSTGRESQL_DATABASE_DSN')
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

async def run():
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

        APP_NAME = "account_app"
        USER_ID = "modepick"
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


if __name__ == "__main__":

    # asyncio.run(update_all_records())
    asyncio.run(remove_duplicate_message())
    asyncio.run(run())
