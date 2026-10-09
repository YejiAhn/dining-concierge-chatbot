from decimal import Decimal, InvalidOperation


def get_slot(slots, name):
    slot = slots.get(name)
    if not slot:
        return None
    return slot.get("value", {}).get("interpretedValue")


def respond(event, action, text=None, slot_name=None):
    session = event["sessionState"]
    intent = session["intent"]

    session["dialogAction"] = {"type": action}

    if action == "Close":
        intent["state"] = "Fulfilled"

    elif action == "ElicitSlot":
        intent["state"] = "InProgress"
        intent["slots"][slot_name] = None
        session["dialogAction"]["slotToElicit"] = slot_name

    result = {"sessionState": session}

    if text:
        result["messages"] = [
            {"contentType": "PlainText", "content": text}
        ]

    return result


def lambda_handler(event, context):
    intent = event["sessionState"]["intent"]
    name = intent["name"]

    # Log the invocation without logging users' personal information.
    print({
        "intent": name,
        "invocationSource": event["invocationSource"]
    })

    if name == "GreetingIntent":
        return respond(event, "Close", "Hi there, how can I help?")

    if name == "ThankYouIntent":
        return respond(event, "Close", "You're welcome!")

    if name == "DiningSuggestionsIntent":
        slots = intent.get("slots") or {}

        location = get_slot(slots, "Location")
        if location and location.strip().casefold() != "manhattan":
            return respond(
                event,
                "ElicitSlot",
                "Sorry, I only support Manhattan. "
                "Where would you like to dine?",
                "Location"
            )

        people = get_slot(slots, "NumberOfPeople")
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
                return respond(
                    event,
                    "ElicitSlot",
                    "Please enter a positive whole number. "
                    "How many people are in your party?",
                    "NumberOfPeople"
                )

        if event["invocationSource"] == "FulfillmentCodeHook":
            return respond(
                event,
                "Close",
                "Thanks! I've collected your dining preferences."
            )

    return respond(event, "Delegate")
