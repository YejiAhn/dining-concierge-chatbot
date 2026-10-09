from datetime import datetime, timezone
import base64
import json
import logging
import os
import secrets
from urllib.request import Request, urlopen

import boto3
from botocore.config import Config


logger = logging.getLogger()
logger.setLevel(logging.INFO)

aws_config = Config(
    connect_timeout=3,
    read_timeout=5,
    retries={"mode": "standard", "total_max_attempts": 2},
)

sqs = boto3.client("sqs", config=aws_config)
ses = boto3.client("ses", config=aws_config)
dynamodb = boto3.resource("dynamodb", config=aws_config)

QUEUE_URL = os.environ["QUEUE_URL"]
TABLE_NAME = os.environ["TABLE_NAME"]
ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"].rstrip("/")
USERNAME = os.environ["OPENSEARCH_USERNAME"]
PASSWORD = os.environ["OPENSEARCH_PASSWORD"]
FROM_EMAIL = os.environ["SES_FROM_EMAIL"]

table = dynamodb.Table(TABLE_NAME)
state_table = dynamodb.Table(os.environ["STATE_TABLE"])


def find_restaurants(cuisine):
    # Randomly select three restaurants matching the cuisine.
    query = {
        "size": 3,
        "_source": ["RestaurantID"],
        "query": {
            "function_score": {
                "query": {
                    "term": {
                        "Cuisine": cuisine
                    }
                },
                "random_score": {
                    "seed": secrets.randbelow(2**31),
                    "field": "_seq_no"
                },
                "boost_mode": "replace"
            }
        }
    }

    credentials = base64.b64encode(
        f"{USERNAME}:{PASSWORD}".encode("utf-8")
    ).decode("ascii")

    request = Request(
        f"{ENDPOINT}/restaurants/_search",
        data=json.dumps(query).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {credentials}"
        },
        method="POST"
    )

    with urlopen(request, timeout=10) as response:
        result = json.load(response)

    if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
        raise RuntimeError("OpenSearch search did not complete successfully.")

    hits = result.get("hits", {}).get("hits", [])
    if not hits:
        raise RuntimeError(f"No restaurants found for cuisine: {cuisine}")

    restaurants = []

    for hit in hits:
        business_id = hit["_source"]["RestaurantID"]

        response = table.get_item(
            Key={"businessId": business_id}
        )
        item = response.get("Item")

        if not item:
            raise RuntimeError(
                f"Restaurant missing from DynamoDB: {business_id}"
            )

        restaurants.append(item)

    return restaurants


def send_recommendations(dining_request, restaurants):
    cuisine = dining_request["cuisine"]
    people = dining_request["numberOfPeople"]
    dining_date = dining_request["diningDate"]
    dining_time = dining_request["diningTime"]
    timezone = dining_request.get("timezone", "America/New_York")

    lines = [
        "Hello!",
        "",
        f"Here are my {cuisine.title()} restaurant suggestions "
        f"for {people} people in {dining_request['location']}, "
        f"on {dining_date} at {dining_time} ({timezone}):",
        ""
    ]

    for number, restaurant in enumerate(restaurants, start=1):
        lines.append(
            f"{number}. {restaurant['name']}, "
            f"located at {restaurant['address']}"
        )
        lines.append(
            f"   Rating: {restaurant['rating']}/5 "
            f"({restaurant['reviewCount']} reviews)"
        )

    lines.extend(["", "Enjoy your meal!"])

    response = ses.send_email(
        Source=FROM_EMAIL,
        Destination={
            "ToAddresses": [dining_request["email"]]
        },
        Message={
            "Subject": {
                "Data": "Your Dining Concierge Restaurant Suggestions",
                "Charset": "UTF-8"
            },
            "Body": {
                "Text": {
                    "Data": "\n".join(lines),
                    "Charset": "UTF-8"
                }
            }
        }
    )

    return response["MessageId"]


def get_previous_restaurants(restaurant_ids, cuisine):
    if (
        not isinstance(restaurant_ids, list)
        or len(restaurant_ids) != 3
        or any(
            not isinstance(business_id, str) or not business_id
            for business_id in restaurant_ids
        )
        or len(set(restaurant_ids)) != 3
    ):
        raise ValueError("Expected three unique previous restaurant IDs.")

    restaurants = []

    for business_id in restaurant_ids:
        response = table.get_item(
            Key={"businessId": business_id}
        )
        item = response.get("Item")

        if not item:
            raise RuntimeError(
                f"Previous restaurant missing: {business_id}"
            )

        if cuisine not in item.get("cuisines", []):
            raise RuntimeError(
                f"Previous restaurant cuisine mismatch: {business_id}"
            )

        restaurants.append(item)

    return restaurants


def lambda_handler(event, context):
    response = sqs.receive_message(
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=2,
        VisibilityTimeout=180
    )

    messages = response.get("Messages", [])

    if not messages:
        logger.info("No dining requests in SQS.")
        return {"processed": 0}

    message = messages[0]

    try:
        dining_request = json.loads(message["Body"])
        cuisine = dining_request["cuisine"].strip().lower()
        dining_request["cuisine"] = cuisine

        reuse = dining_request.get("reusePrevious") is True

        if reuse:
            restaurants = get_previous_restaurants(
                dining_request.get("restaurantIds"),
                cuisine
            )
        else:
            restaurants = find_restaurants(cuisine)

        email_id = send_recommendations(
            dining_request, restaurants
        )

        # Save the recommendation after SES accepts the email.
        # Older queued requests may not have userId.
        user_id = dining_request.get("userId")

        if user_id:
            state_table.put_item(
                Item={
                    "userId": user_id,
                    "location": dining_request["location"],
                    "cuisine": cuisine,
                    "restaurantIds": [
                        restaurant["businessId"]
                        for restaurant in restaurants
                    ],
                    "updatedAt": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "requestId": dining_request["requestId"]
                }
            )

        # Delete after both sending and saving succeed.
        sqs.delete_message(
            QueueUrl=QUEUE_URL,
            ReceiptHandle=message["ReceiptHandle"]
        )

        logger.info(
            "Processed requestId=%s; reused=%s; sesMessageId=%s",
            dining_request.get("requestId", message["MessageId"]),
            reuse,
            email_id
        )

        return {
            "processed": 1,
            "reusedPrevious": reuse,
            "restaurantCount": len(restaurants),
            "sesMessageId": email_id
        }

    except Exception:
        logger.exception(
            "Failed to process SQS messageId=%s",
            message["MessageId"]
        )
        raise
