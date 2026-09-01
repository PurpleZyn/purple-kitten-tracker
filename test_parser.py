from tracker import extract_kitten_event, normalize_logs

CONFIG = {
    "kitten_item_id": 215,
    "item_receive_log_id": 4103,
}

sample_v2 = {
    "log": [
        {
            "id": "abc123",
            "timestamp": 1700000000,
            "details": {
                "id": 4103,
                "title": "Item receive",
                "category": "Item sending",
            },
            "data": {
                "sender": 1234567,
                "items": {"215": [250, 0]},
                "message": "",
            },
            "params": {},
        }
    ]
}

entry = normalize_logs(sample_v2)[0]
event = extract_kitten_event(entry, CONFIG)

assert event["sender_id"] == "1234567"
assert event["quantity"] == 250
print("Parser self-test passed.")
