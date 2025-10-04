from google.adk.agents import LlmAgent
from pydantic import BaseModel

from llms.openai import MODEL_GPT_5_MINI


class DividedMessageOutput(BaseModel):
    transaction_type: str
    amount: int
    currency: str
    transaction_party: str


card_message_divider_agent = LlmAgent(
    name="card_message_divider_agent",
    model=MODEL_GPT_5_MINI,
    description="문자 메세지를 분해합니다.",
    output_schema=DividedMessageOutput,
    disallow_transfer_to_parent= True,  # 부모 에이전트로 전환 금지
    disallow_transfer_to_peers= True,  # 동료 에이전트로 전환 금지
    instruction="""
넌 카드 승인 문자 메시지를 분석하는 전문가야. 사용자가 제공한 문자 메시지를 읽고, 다음 정보를 정확하게 추출해.

**분석 기준:**
1. **transaction_type:**
   - "승인": 승인 (돈이 지출됨)
   - "승인취소": 승인 취소 (지출된 돈이 환불됨)
   - "거절": 거절 (거래가 거부되어 돈이 지출되지 않음)
   - "N": 처리 불가능한 문자 (카드 승인 관련 문자가 아님). 이때는 다른 모든 변수도 전부 ""으로 처리

2. **amount:**
   - 문자에 표시된 거래 금액 (숫자만, 쉼표 제외)
   - 예: "121,000" → 121000

3. **currency:**
   - "KRW"
   - "JPY"
   - "USD"
   - "EUR"
   중 하나.

4. **transaction_party:**
   - 문자에 표시된 가맹점명을 **원본 그대로** 추출
   - 편집하거나 수정하지 말 것

이제 다음 문자 메시지를 분석하세요:
"""
)


bank_message_divider_agent = LlmAgent(
    name="bank_message_divider_agent",
    model=MODEL_GPT_5_MINI,
    description="문자 메세지를 분해합니다.",
    output_schema=DividedMessageOutput,
    disallow_transfer_to_parent= True,  # 부모 에이전트로 전환 금지
    disallow_transfer_to_peers= True,  # 동료 에이전트로 전환 금지
    instruction="""
넌 은행 승인 문자 메시지를 분석하는 전문가야. 사용자가 제공한 문자 메시지를 읽고, 다음 정보를 정확하게 추출해.

**분석 기준:**
1. **transaction_type:**
   - "입금": 입금 (돈이 입금됨)
   - "출금": 출금 (돈이 출금됨)
   - "거절": 거절 (거래가 거부되어 돈이 변화되지 않음)
   - "N": 처리 불가능한 문자 (은행 관련 문자가 아님). 이때는 다른 모든 변수도 전부 ""으로 처리

2. **amount:**
   - 문자에 표시된 거래 금액 (숫자만, 쉼표 제외)
   - 예: "121,000" → 121000

3. **currency:**
   - "KRW"
   - "JPY"
   - "USD"
   - "EUR"
   중 하나.

4. **transaction_party:**
   - 문자에 표시된 거래상대 **원본 그대로** 추출
   - 편집하거나 수정하지 말 것

이제 다음 문자 메시지를 분석하세요:
"""
)
