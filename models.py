from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Float, MetaData
from sqlalchemy.orm import declarative_base
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.sql import func
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    BigInteger,
    ForeignKey,
    Date,

)
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
    idtbl_receipt = Column(BigInteger, ForeignKey("tbl_receipt.idtbl_receipt"))




class Receipt(Base):
    __tablename__ = "tbl_receipt"

    idtbl_receipt = Column(BigInteger, primary_key=True)
    taxfree = Column(Boolean, default=None)
    taxfree_return_cash = Column(Integer, default=None)
    pay_personal_card = Column(Boolean, default=None)
    receipt_image_path = Column(MEDIUMTEXT, nullable=False)
    receipt_currency = Column(Integer)  # 0은 엔화, 1은 원화
    receipt_price = Column(Integer)
    cash_receipt_price = Column(Integer)

    memo = Column(String(200), default=None)
    need_return_currency = Column(Integer)
    need_return_price = Column(Integer)
    need_return_image_path = Column(String(100), default=None)
    forwarder_receipt_image_path = Column(String(100), default=None)
    user_id = Column(Integer, ForeignKey("tbl_users.idtbl_users"))
    manager_confirmed = Column(Boolean, default=False)
    buying_date = Column(Date, default=None)
    created_time = Column(DateTime, default=func.current_timestamp())
    update_time = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
