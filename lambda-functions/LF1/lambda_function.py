import json
import os
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import BotoCoreError, ClientError


sqs = boto3.client("sqs")
NY_TIMEZONE = ZoneInfo("America/New_York")
CUISINES = {"chinese", "japanese", "italian", "indian", "mexican"}

PROMPTS = {
    "Location": "Where would you like to dine? I support Manhattan.",
    "Cuisine": "What cuisine would you like? Chinese, Japanese, "
               "Italian, Indian, or Mexican?",
    "NumberOfPeople": "How many people are in your party?",
    "DiningDate": "What date would you like to dine?",
    "DiningTime": "What time would you like to dine? "
                  "Please include AM or PM.",
    "Email": "What email address should I send the suggestions to?"
}


def get_slot(slots, name):
    slot = slots.get(name)
    if not slot:
        return None
    value = (slot.get("value") or {}).get("interpretedValue")
    return value.strip() if isinstance(value, str) else None


def respond(event, action, text=None, slot_name=None, state="Fulfilled"):
    session = event["sessionState"]
    intent = session["intent"]
    session["dialogAction"] = {"type": action}

    if action == "Close":
        intent["state"] = state
    elif action == "ElicitSlot":
        intent["state"] = "InProgress"
        intent.setdefault("slots", {})[slot_name] = None
        session["dialogAction"]["slotToElicit"] = slot_name

    result = {"sessionState": session}
    if text:
        result["messages"] = [
            {"contentType": "PlainText", "content": text}
        ]
    return result


def validate(slots):
    values = {name: get_slot(slots, name) for name in PROMPTS}

    location = values["Location"]
    if location is not None and location.casefold() != "manhattan":
        return "Location", "Sorry, I only support Manhattan. " \
                           "Where would you like to dine?"

    cuisine = values["Cuisine"]
    if cuisine is not None and cuisine.casefold() not in CUISINES:
        return "Cuisine", PROMPTS["Cuisine"]

    people = values["NumberOfPeople"]
    if people is not None:
        try:
            number = Decimal(people)
            valid = (
                number.is_finite()
                and number >= 1
                and number == number.to_integral_value()
            )
        except InvalidOperation:
            valid = False
        if not valid:
            return "NumberOfPeople", \
                "Please enter a positive whole number. " \
                "How many people are in your party?"

    now = datetime.now(NY_TIMEZONE)
    dining_date = None
    date_value = values["DiningDate"]

    if slots.get("DiningDate") is not None:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value or ""):
                raise ValueError
            dining_date = date.fromisoformat(date_value)
        except ValueError:
            return "DiningDate", \
                "Please enter a specific date, such as YYYY-MM-DD."

        if dining_date < now.date():
            return "DiningDate", \
                "Please choose today or a future date."

    dining_time = None
    time_value = values["DiningTime"]

    if slots.get("DiningTime") is not None:
        try:
            if not re.fullmatch(r"\d{2}:\d{2}", time_value or ""):
                raise ValueError
            dining_time = time.fromisoformat(time_value)
        except ValueError:
            return "DiningTime", \
                "Please enter a specific time, such as 7 PM."

    if dining_date is not None and dining_time is not None:
        dining_at = datetime.combine(
            dining_date, dining_time, tzinfo=NY_TIMEZONE
        )
        if dining_at <= now:
            return "DiningTime", \
                "That time has already passed. " \
                "Please choose a future time on your selected date."

    email = values["Email"]
    if email is not None and not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+", email
    ):
        return "Email", \
            "Please enter a valid email address, " \
            "such as name@example.com."

    return None


def lambda_handler(event, context):
    intent = event["sessionState"]["intent"]
    name = intent["name"]
    source = event["invocationSource"]

    print({"intent": name, "invocationSource": source})

    if name == "GreetingIntent":
        return respond(event, "Close", "Hi there, how can I help?")

    if name == "ThankYouIntent":
        return respond(event, "Close", "You're welcome!")

    if name != "DiningSuggestionsIntent":
        return respond(event, "Delegate")

    slots = intent.get("slots") or {}
    invalid = validate(slots)

    if invalid:
        slot_name, prompt = invalid
        return respond(event, "ElicitSlot", prompt, slot_name)

    if source == "DialogCodeHook":
        return respond(event, "Delegate")

    # Check completeness again before sending a request.
    values = {name: get_slot(slots, name) for name in PROMPTS}
    for slot_name, value in values.items():
        if not value:
            return respond(
                event, "ElicitSlot", PROMPTS[slot_name], slot_name
            )

    request = {
        "requestId": context.aws_request_id,
        "location": "Manhattan",
        "cuisine": values["Cuisine"].casefold(),
        "numberOfPeople": int(Decimal(values["NumberOfPeople"])),
        "diningDate": values["DiningDate"],
        "diningTime": values["DiningTime"],
        "timezone": "America/New_York",
        "email": values["Email"],
        "createdAt": datetime.now(NY_TIMEZONE).isoformat()
    }

    try:
        result = sqs.send_message(
            QueueUrl=os.environ["QUEUE_URL"],
            MessageBody=json.dumps(request)
        )
    except (BotoCoreError, ClientError):
        print("Failed to send dining request to SQS.")
        return respond(
            event,
            "Close",
            "Sorry, I couldn't submit your request. Please try again.",
            state="Failed"
        )

    print({"sqsMessageId": result["MessageId"]})

    return respond(
        event,
        "Close",
        "You're all set! I've received your request. "
        "I'll email you restaurant suggestions shortly."
    )
