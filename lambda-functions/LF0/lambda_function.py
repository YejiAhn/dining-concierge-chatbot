import json


def lambda_handler(event, context):
    response = {
        "messages": [
            {
                "type": "unstructured",
                "unstructured": {
                    "text": (
                        "I'm still under development. "
                        "Please come back later."
                    )
                }
            }
        ]
    }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(response)
    }
