from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Float, MetaData
from sqlalchemy.orm import declarative_base

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