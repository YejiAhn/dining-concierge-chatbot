import json
import os
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import BotoCoreError, ClientError


sqs = boto3.client("sqs")
state_table = boto3.resource("dynamodb").Table(
    os.environ["STATE_TABLE"]
)
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

    values = {
        slot_name: get_slot(slots, slot_name)
        for slot_name in PROMPTS
    }

    for slot_name, value in values.items():
        if not value:
            if source == "DialogCodeHook":
                return respond(event, "Delegate")

            return respond(
                event, "ElicitSlot", PROMPTS[slot_name], slot_name
            )

    user_id = event["sessionId"]
    cuisine = values["Cuisine"].casefold()
    attributes = event["sessionState"].setdefault(
        "sessionAttributes", {}
    )

    # Keep the exact previous IDs while the user answers the question.
    cached = attributes.get("previousRecommendation")
    previous = None

    if cached:
        previous = json.loads(cached)

        if (
            previous.get("location") != "Manhattan"
            or previous.get("cuisine") != cuisine
        ):
            previous = None
            attributes.pop("previousRecommendation", None)

    if previous is None:
        try:
            previous = state_table.get_item(
                Key={"userId": user_id},
                ConsistentRead=True
            ).get("Item")
        except (BotoCoreError, ClientError):
            print("Failed to read recommendation history.")
            return respond(
                event,
                "Close",
                "Sorry, I couldn't check your previous request. "
                "Please try again.",
                state="Failed"
            )

    same_search = (
        previous is not None
        and previous.get("location") == "Manhattan"
        and previous.get("cuisine") == cuisine
        and bool(previous.get("restaurantIds"))
    )

    reuse = False
    restaurant_ids = []

    if same_search:
        choice = get_slot(slots, "ReusePrevious")
        choice = choice.casefold() if choice else None

        if choice not in {"yes", "no"}:
            attributes["previousRecommendation"] = json.dumps({
                "location": previous["location"],
                "cuisine": previous["cuisine"],
                "restaurantIds": previous["restaurantIds"]
            })

            return respond(
                event,
                "ElicitSlot",
                "You previously requested "
                f"{cuisine.title()} restaurants in Manhattan. "
                "Would you like the same restaurant recommendations "
                "as last time? Please say yes or no.",
                "ReusePrevious"
            )

        reuse = choice == "yes"
        if reuse:
            restaurant_ids = previous["restaurantIds"]

    if source == "DialogCodeHook":
        return respond(event, "Delegate")

    request = {
        "requestId": context.aws_request_id,
        "userId": user_id,
        "location": "Manhattan",
        "cuisine": cuisine,
        "numberOfPeople": int(Decimal(values["NumberOfPeople"])),
        "diningDate": values["DiningDate"],
        "diningTime": values["DiningTime"],
        "timezone": "America/New_York",
        "email": values["Email"],
        "createdAt": datetime.now(NY_TIMEZONE).isoformat(),
        "reusePrevious": reuse,
        "restaurantIds": restaurant_ids
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

    attributes.pop("previousRecommendation", None)
    print({"sqsMessageId": result["MessageId"]})

    text = (
        "You're all set! I'll email you the same restaurant "
        "recommendations with your updated dining details."
        if reuse
        else "You're all set! I'll email you new restaurant "
             "suggestions shortly."
    )

    return respond(event, "Close", text)
