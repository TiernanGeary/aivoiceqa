"""Mock responses for local development without a running reco instance."""

MOCK_CALL_ID = "call_mock_abc123"
MOCK_CONVERSATION_ID = 42

MOCK_TRANSCRIPT = (
    "ASSISTANT: お電話ありがとうございます。予約受付センターでございます。\n"
    "USER: はい、予約をお願いします。\n"
    "ASSISTANT: かしこまりました。ご希望の日時をお伺いしてもよろしいでしょうか。\n"
    "USER: 来週の月曜日の午後2時でお願いします。\n"
    "ASSISTANT: 来週月曜日、午後2時ですね。お名前をお伺いしてもよろしいでしょうか。\n"
    "USER: 田中太郎です。\n"
    "ASSISTANT: 田中太郎様ですね。来週月曜日午後2時でご予約を承りました。"
    " ご確認のメッセージをお送りいたします。本日はお電話ありがとうございました。"
)

MOCK_CONVERSATION_DATA = {
    "id": MOCK_CONVERSATION_ID,
    "call_status": "success",
    "duration_seconds": 65,
    "phone_number": "+819012345678",
    "flow_path": "booking/happy_path",
    "customer_id": "cust_mock_001",
    "created_at": "2026-03-10T10:00:00Z",
}

MOCK_RECORDING_URL = "https://example.com/mock-recordings/call_mock_abc123.wav"
