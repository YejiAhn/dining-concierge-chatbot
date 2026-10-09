import getpass
import json
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import boto3


REGION = "us-east-1"
TABLE_NAME = "yelp-restaurants"


def main():
    api_key = getpass.getpass("Yelp API key: ").strip()
    if not api_key:
        raise ValueError("Yelp API key is required.")

    params = urlencode({
        "term": "Japanese restaurants",
        "location": "Manhattan, NY",
        "categories": "japanese",
        "limit": 5,
    })

    request = Request(
        f"https://api.yelp.com/v3/businesses/search?{params}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=20) as response:
        data = json.load(response, parse_float=Decimal)

    businesses = data.get("businesses", [])
    if not businesses:
        raise RuntimeError("Yelp returned no restaurants.")

    table = boto3.resource(
        "dynamodb",
        region_name=REGION,
    ).Table(TABLE_NAME)

    timestamp = datetime.now(timezone.utc).isoformat()
    saved = 0

    for business in businesses:
        location = business.get("location", {})

        item = {
            "businessId": business["id"],
            "name": business["name"],
            "address": ", ".join(
                location.get("display_address", [])
            ),
            "coordinates": business.get("coordinates", {}),
            "reviewCount": business.get("review_count", 0),
            "rating": business.get("rating", 0),
            "zipCode": location.get("zip_code", ""),
            "cuisines": ["japanese"],
            "insertedAtTimestamp": timestamp,
        }

        table.put_item(Item=item)
        saved += 1
        print(f"Saved: {item['name']} ({item['businessId']})")

    print(f"\nSuccessfully saved {saved} restaurants to {TABLE_NAME}.")


if __name__ == "__main__":
    main()
