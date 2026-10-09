import json
import os
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError


lex = boto3.client("lexv2-runtime", region_name="us-east-1")


def api_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(payload)
    }


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        message = body["messages"][0]["unstructured"]
        text = message["text"]
        session_id = message["id"]

        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1024:
            raise ValueError("Invalid message text")

        if not isinstance(session_id, str) or not re.fullmatch(
            r"[0-9A-Za-z._:-]{2,100}", session_id
        ):
            raise ValueError("Invalid session ID")

    except (ValueError, KeyError, IndexError, TypeError):
        return api_response(400, {
            "code": 400,
            "message": "Provide message text and a valid session ID."
        })

    try:
        result = lex.recognize_text(
            botId=os.environ["LEX_BOT_ID"],
            botAliasId=os.environ["LEX_BOT_ALIAS_ID"],
            localeId=os.environ["LEX_LOCALE_ID"],
            sessionId=session_id,
            text=text.strip()
        )
    except (BotoCoreError, ClientError) as error:
        # Log the error type without logging the user's message.
        print({"lexErrorType": type(error).__name__})
        return api_response(502, {
            "code": 502,
            "message": "Unable to contact the chatbot. Please try again."
        })

    messages = [
        {
            "type": "unstructured",
            "unstructured": {
                "text": message["content"]
            }
        }
        for message in result.get("messages", [])
        if message.get("contentType") == "PlainText"
        and message.get("content")
    ]

    if not messages:
        messages = [
            {
                "type": "unstructured",
                "unstructured": {
                    "text": "What else can I help you with?"
                }
            }
        ]

    return api_response(200, {"messages": messages})
