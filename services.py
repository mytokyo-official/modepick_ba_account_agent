import datetime
import asyncio
import re
import traceback
from typing import List, Tuple, Optional
from sqlalchemy import select
from rapidfuzz import fuzz
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from database import get_database_session
from models import 장부_결제문자, Receipt
from config import CARD_SENDER_LIST, BANK_SENDER_LIST, APP_NAME, USER_ID, SLACK_ERROR_LOG_CHANNEL_ID, \
    SLACK_ACCOUNT_CHANNEL_ID, SLACK_REACT_APP_CHANNEL_ID
from agents.account_classifier import account_classifier, AccountClassificationOutput
from agents.message_divider_agent import card_message_divider_agent, DividedMessageOutput, bank_message_divider_agent

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

def similarity(a, b):
    """두 문자열의 유사도 계산"""
    return fuzz.ratio(a, b)

async def infer_account(_app):
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

        session_count = 0

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

async def message_divider_run():
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

async def check_last_message_upload(_app):
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
                        await _app.client.chat_postMessage(
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
                        await _app.client.chat_postMessage(
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
        await _app.client.chat_postMessage(
            channel=SLACK_ERROR_LOG_CHANNEL_ID,
            text=traceback.format_exc()
        )

async def update_cancel_transactions():
    """승인취소 거래와 매칭되는 승인 거래들의 거래목적을 '취소건'으로 업데이트"""
    db_session = await get_database_session()
    try:
        # 승인취소 거래들 조회
        refund_stmt = select(장부_결제문자).filter(
            장부_결제문자.transaction_type == '승인취소',
            장부_결제문자.거래목적.is_(None)
        )
        refund_result = await db_session.execute(refund_stmt)
        refund_rows = refund_result.scalars().all()
        
        # 승인 거래들 조회
        approval_stmt = select(장부_결제문자).filter(
            장부_결제문자.transaction_type == '승인'
        )
        approval_result = await db_session.execute(approval_stmt)
        approval_rows = approval_result.scalars().all()
        
        updated_count = 0
        
        for refund_row in refund_rows:
            # amount와 currency가 동일한 승인 거래들 찾기
            matching_approvals = [
                approval for approval in approval_rows
                if approval.amount == refund_row.amount and approval.currency == refund_row.currency
            ]

            if not matching_approvals:
                print("찾을수 없음.", refund_row.message)
            
            # 거래상대 유사도가 0.8 이상인 것 찾기
            for approval in matching_approvals:
                if approval.거래상대 and refund_row.거래상대:
                    similarity_score = similarity(refund_row.거래상대, approval.거래상대)
                    if similarity_score >= 80:
                        # 매칭되는 승인 거래와 승인취소 거래 모두 거래목적을 '취소건'으로 설정
                        approval.거래목적 = "취소건"
                        refund_row.거래목적 = "취소건"
                        updated_count += 2
                        
                        # print(f"취소건 매칭: 승인({approval.mac_message_id}) <-> 승인취소({refund_row.mac_message_id}) (유사도: {similarity_score:.1f}%)")
        
        await db_session.commit()
        print(f"총 {updated_count}개의 거래가 '취소건'으로 업데이트되었습니다.")
        
    finally:
        await db_session.close()

async def link_receipt_to_payments():
    """거래목적이 '판매용상품'인 결제문자와 Receipt를 매칭하여 연결"""
    db_session = await get_database_session()
    try:
        filter_date = datetime.datetime(2025, 10, 1, 0, 0, 0)

        # 거래목적이 '판매용상품'인 결제문자들 조회 (아직 Receipt와 연결되지 않은 것들)
        payment_stmt = select(장부_결제문자).filter(
            장부_결제문자.거래목적 == '판매용상품',
            장부_결제문자.idtbl_receipt.is_(None),
            장부_결제문자.결제시간 >= filter_date
        ).order_by(장부_결제문자.결제시간)
        payment_result = await db_session.execute(payment_stmt)
        payment_rows = payment_result.scalars().all()
        
        # 모든 Receipt 조회
        receipt_stmt = select(Receipt)
        receipt_result = await db_session.execute(receipt_stmt)
        receipt_rows = receipt_result.scalars().all()
        
        linked_count = 0
        
        for payment_row in payment_rows:
            # amount와 currency가 매칭되는 Receipt들 찾기
            matching_receipts = []
            
            for receipt in receipt_rows:
                # currency 매칭 확인 (0: JPY, 1: KRW)
                payment_currency = payment_row.currency
                receipt_currency_code = "JPY" if receipt.receipt_currency == 0 else "KRW"
                receipt_amount = (receipt.cash_receipt_price or 0) + (receipt.receipt_price or 0)

                if (payment_currency == receipt_currency_code and 
                    payment_row.amount == receipt_amount):
                    
                    # 날짜 차이 확인 (30일 이내)
                    if payment_row.결제시간 and receipt.buying_date:
                        # 결제시간은 datetime, buying_date는 date이므로 date로 변환하여 비교
                        payment_date = payment_row.결제시간.date()
                        date_diff = abs((payment_date - receipt.buying_date).days)
                        
                        if date_diff <= 30:
                            matching_receipts.append((receipt, date_diff))
            
            # 날짜 차이가 가장 작은 Receipt와 매칭
            if matching_receipts:
                # 날짜 차이 순으로 정렬하여 가장 가까운 것 선택
                matching_receipts.sort(key=lambda x: x[1])
                best_receipt = matching_receipts[0][0]
                date_diff = matching_receipts[0][1]
                
                # 연결 설정
                payment_row.idtbl_receipt = best_receipt.idtbl_receipt
                linked_count += 1
                
                print(f"Receipt 연결: {payment_row.mac_message_id} -> Receipt {best_receipt.idtbl_receipt} "
                      f"(금액: {payment_row.amount} {payment_row.currency}, 날짜차이: {date_diff}일)")
        
        await db_session.commit()
        print(f"총 {linked_count}개의 결제문자가 Receipt와 연결되었습니다.")
        
    finally:
        await db_session.close()

async def send_unlinked_receipts_to_slack(_app):
    """Receipt와 연결되지 않은 판매용상품 거래를 슬랙으로 전송"""
    db_session = await get_database_session()
    try:
        # KST 2025-10-01 00:00:00
        kst_filter_date = datetime.datetime(2025, 10, 1, 0, 0, 0)
        # KST에서 UTC로 변환 (KST = UTC + 9시간이므로 UTC = KST - 9시간)
        filter_date = kst_filter_date - datetime.timedelta(hours=9)
        
        # 조건에 맞는 레코드들 조회
        stmt = select(장부_결제문자).filter(
            장부_결제문자.idtbl_receipt.is_(None),
            장부_결제문자.거래목적 == '판매용상품',
            장부_결제문자.결제시간 >= filter_date
        ).order_by(장부_결제문자.결제시간)
        
        result = await db_session.execute(stmt)
        rows = result.scalars().all()
        
        if not rows:
            print("조건에 맞는 미연결 거래가 없습니다.")
            return
        
        # 각 거래를 개별 메시지로 전송
        sent_count = 0
        for row in rows:
            # KST 기준으로 날짜 포맷팅 (MM/dd HH:mm)
            if row.결제시간:
                # UTC에서 KST로 변환 (+9시간)
                kst_time = row.결제시간 + datetime.timedelta(hours=9)
                date_str = kst_time.strftime("%m/%d %H:%M")
            else:
                date_str = "미확인"
            amount_str = f"{row.amount:,}{row.currency} |아이디: {row.mac_message_id}" if row.amount else "미확인"
            
            message = f"영수증 없음:pleading_face: {date_str} | {row.발신자명 or '미확인'} | {row.거래상대 or '미확인'} | {amount_str}"
            
            try:
                await _app.client.chat_postMessage(
                    channel=SLACK_REACT_APP_CHANNEL_ID,
                    text=message
                )
                sent_count += 1
            except Exception as e:
                print(f"Slack 메시지 전송 실패: {e}")
        
        print(f"미연결 거래 {sent_count}/{len(rows)}건을 슬랙으로 전송했습니다.")
        
    finally:
        await db_session.close()
