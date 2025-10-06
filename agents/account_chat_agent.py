from google.adk.agents import LlmAgent
from pydantic import BaseModel, Field
from typing import Literal

from agents.account_classifier import AccountClassificationOutput
from llms.openai import MODEL_GPT_5_MINI



account_chat_agent = LlmAgent(
    name="account_chat_agent",
    model=MODEL_GPT_5_MINI,
    output_schema=AccountClassificationOutput,
    disallow_transfer_to_parent= True,  # 부모 에이전트로 전환 금지
    disallow_transfer_to_peers= True,  # 동료 에이전트로 전환 금지
    instruction="""
당신은 한국의 회계 전문가입니다. 당신이 이미 제공한 문자메세지를 분석해서 business_purpose, main_category, sub_category를 사용자에게 줬었습니다.
하지만 사용자가 채팅을 통해 더 적합한 business_purpose, main_category, sub_category를 제공하는 상황입니다. 

사용자의 채팅을 분석해서 business_purpose, main_category, sub_category를 추출하세요.
모자란 내용이 있다면 모자란 내용은 ""로 비워두고 있는만큼만 채우세요.
사용자가 왜 바꾸는지에 대한 이유를 말한다면, reason에 적어주세요. "~~라고 슬랙에서 말했음."이라고 붙여주세요. 이유가 없다면 ""로 비워두세요.
confidence는 반드시 1.0으로 고정해주세요.

이제 다음 메시지를 분석하세요:
"""
)