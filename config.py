import os
import dotenv

dotenv.load_dotenv()

# Database configuration
POSTGRESQL_DATABASE_DSN = os.getenv('POSTGRESQL_DATABASE_DSN')

# Slack configuration
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_APP_TOKEN = os.getenv('SLACK_APP_TOKEN')
SLACK_ACCOUNT_CHANNEL_ID = os.getenv('SLACK_ACCOUNT_CHANNEL_ID')
SLACK_ERROR_LOG_CHANNEL_ID = os.getenv('SLACK_ERROR_LOG_CHANNEL_ID')

# Application configuration
APP_NAME = "account_app"
USER_ID = "modepick"

# Sender lists
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